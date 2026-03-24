"""
bot.py — Telegram bot message handling for GAVIN.

Parses incoming Telegram updates, enforces the allowlist, and kicks off
the GAVIN pipeline asynchronously so webhook responses are fast.

Episode feedback flow:
  1. Episode publishes → log_episode() called
  2. 60 minutes later → bot sends "How was this episode?" with ★ inline buttons
  3. User taps 1-5 stars → bot records rating, asks for optional text feedback
  4. User replies with text (or /skip) → record_feedback() called, Haiku re-synthesizes
"""

import asyncio
import logging
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import config
from pipeline import run_gavin_pipeline, PipelineError
from memory import record_feedback, load_episode_memory, load_company_memory, save_company_memory

logger = logging.getLogger("gavin.bot")

# Module-level bot application — initialized in app.py on startup
_application: Application | None = None

# ── Feedback flow state ────────────────────────────────────────────────────────
# These track in-progress feedback collection per chat_id.

# chat_id → topic string: episode awaiting text feedback after a star rating
_awaiting_text: dict[int, str] = {}

# chat_id → True: bot is waiting for /memory update text
_awaiting_memory: dict[int, bool] = {}

# Delay before asking for feedback (60 minutes)
_FEEDBACK_DELAY_SECONDS = 60 * 60


def get_application() -> Application:
    """
    Return the initialized Telegram Application instance.

    Must be called after initialize_bot() has run.
    """
    if _application is None:
        raise RuntimeError("Telegram bot application has not been initialized. Call initialize_bot() first.")
    return _application


async def initialize_bot() -> Application:
    """
    Build and initialize the Telegram Application.

    Sets up all command and message handlers. Called once on FastAPI startup.

    Returns:
        The initialized Application instance.
    """
    global _application

    logger.info("Initializing Telegram bot application...")

    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", _handle_start))
    application.add_handler(CommandHandler("help", _handle_start))
    application.add_handler(CommandHandler("skip", _handle_skip))
    application.add_handler(CommandHandler("memory", _handle_memory))
    application.add_handler(CommandHandler("lessons", _handle_lessons))
    application.add_handler(CommandHandler("episodes", _handle_episodes))

    # Inline keyboard callback (star ratings)
    application.add_handler(CallbackQueryHandler(_handle_rating_callback, pattern=r"^rate:"))

    # Plain text — handles topics AND feedback text
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text)
    )

    await application.initialize()
    _application = application

    logger.info("Telegram bot application initialized.")
    return application


async def process_update(update_data: dict[str, Any]) -> None:
    """
    Parse a raw webhook payload dict and dispatch it to the Application.

    Called by the FastAPI webhook endpoint for every incoming Telegram update.

    Args:
        update_data: The raw JSON dict from the Telegram webhook POST body.
    """
    app = get_application()
    update = Update.de_json(update_data, app.bot)
    await app.process_update(update)


# ── Command Handlers ───────────────────────────────────────────────────────────

async def _handle_start(update: Update, context: Any) -> None:
    """Handle /start and /help commands."""
    if not _is_authorized(update):
        return

    welcome = (
        "👋 Hey! I'm GAVIN — Generate Audio Via Intelligent Narration.\n\n"
        "Send me any book title or topic and I'll research it, narrate it, "
        "and publish it as a podcast episode to your private feed.\n\n"
        "Just type a title and I'll get to work! For example:\n"
        "• Good to Great by Jim Collins\n"
        "• The psychology of habits\n"
        "• Naval Ravikant on wealth and happiness\n\n"
        "Commands:\n"
        "/memory — view or update company context\n"
        "/lessons — view synthesized episode lessons\n"
        "/episodes — view rated episode history"
    )
    await update.message.reply_text(welcome)


async def _handle_skip(update: Update, context: Any) -> None:
    """Handle /skip — cancel pending feedback text entry."""
    if not _is_authorized(update):
        return

    chat_id = update.effective_chat.id

    if chat_id in _awaiting_text:
        topic = _awaiting_text.pop(chat_id)
        await update.message.reply_text(
            f"No problem — feedback skipped for \"{topic}\"."
        )
    elif chat_id in _awaiting_memory:
        _awaiting_memory.pop(chat_id)
        await update.message.reply_text("Memory update cancelled.")
    else:
        await update.message.reply_text("Nothing to skip right now.")


async def _handle_memory(update: Update, context: Any) -> None:
    """
    Handle /memory — show the current company memory and offer to update it.

    If the user follows up with text, that text replaces the company memory
    on the Railway volume (takes effect immediately, no redeploy needed).
    """
    if not _is_authorized(update):
        return

    chat_id = update.effective_chat.id
    company_memory = load_company_memory()

    if company_memory:
        preview = company_memory[:800] + ("..." if len(company_memory) > 800 else "")
        await update.message.reply_text(
            f"📝 Current company memory:\n\n{preview}\n\n"
            f"Reply with new text to update it, or /skip to cancel."
        )
    else:
        await update.message.reply_text(
            "No company memory found. Reply with your company context to set it, "
            "or /skip to cancel."
        )

    _awaiting_memory[chat_id] = True


async def _handle_lessons(update: Update, context: Any) -> None:
    """Handle /lessons — show the current synthesized lessons doc."""
    if not _is_authorized(update):
        return

    memory = load_episode_memory()
    lessons = memory.get("lessons", "").strip()

    if not lessons:
        await update.message.reply_text(
            "No lessons yet — rate a few episodes and GAVIN will synthesize patterns "
            "from your feedback automatically."
        )
        return

    await update.message.reply_text(
        f"📚 Lessons from past episodes:\n\n{lessons}"
    )


async def _handle_episodes(update: Update, context: Any) -> None:
    """Handle /episodes — show rated episode history."""
    if not _is_authorized(update):
        return

    memory = load_episode_memory()
    episodes = memory.get("episodes", [])

    if not episodes:
        await update.message.reply_text("No episodes in memory yet.")
        return

    lines = []
    for ep in reversed(episodes[-20:]):  # Show most recent 20
        stars = "★" * (ep["rating"] or 0) + "☆" * (5 - (ep["rating"] or 0))
        rating_str = f"{stars}" if ep["rating"] else "unrated"
        line = f"• {ep['topic']} — {rating_str}"
        if ep.get("feedback"):
            line += f"\n  \"{ep['feedback'][:80]}{'...' if len(ep.get('feedback','')) > 80 else ''}\""
        lines.append(line)

    total = len(episodes)
    rated = sum(1 for ep in episodes if ep["rating"] is not None)
    avg = (
        sum(ep["rating"] for ep in episodes if ep["rating"]) / rated
        if rated > 0 else 0
    )

    header = f"🎙️ Episode history ({total} total, {rated} rated"
    if rated > 0:
        header += f", avg {avg:.1f}★"
    header += "):\n\n"

    await update.message.reply_text(header + "\n".join(lines))


# ── Text Handler ───────────────────────────────────────────────────────────────

async def _handle_text(update: Update, context: Any) -> None:
    """
    Handle plain text messages.

    Routing:
      1. If awaiting memory update → save new company memory
      2. If awaiting feedback text → record feedback for pending episode
      3. Otherwise → treat as new topic, kick off pipeline
    """
    if not _is_authorized(update):
        return

    text = update.message.text.strip()
    if not text:
        return

    chat_id = update.effective_chat.id

    # ── Memory update flow ────────────────────────────────────────────────────
    if chat_id in _awaiting_memory:
        _awaiting_memory.pop(chat_id)
        success = save_company_memory(text)
        if success:
            await update.message.reply_text(
                "✅ Company memory updated! GAVIN will use this context starting from the next episode."
            )
        else:
            await update.message.reply_text(
                "❌ Failed to save company memory. Check Railway logs."
            )
        return

    # ── Feedback text flow ────────────────────────────────────────────────────
    if chat_id in _awaiting_text:
        topic = _awaiting_text.pop(chat_id)
        logger.info(f"Recording feedback text for '{topic}': '{text[:80]}'")

        # We need the rating that was already saved — find it
        memory = load_episode_memory()
        rating = None
        for ep in reversed(memory["episodes"]):
            if ep["topic"] == topic and ep["rating"] is not None:
                rating = ep["rating"]
                break

        if rating is not None:
            await record_feedback(topic, rating, text)
            await update.message.reply_text(
                "✅ Feedback saved! GAVIN will learn from this for future episodes."
            )
        else:
            await update.message.reply_text(
                "Thanks for the feedback! (Note: couldn't find the matching episode to attach it to.)"
            )
        return

    # ── New topic ─────────────────────────────────────────────────────────────
    topic = text
    logger.info(f"Received topic from user {update.effective_user.id}: '{topic}'")

    await update.message.reply_text(
        f"🎙️ GAVIN here! Starting research on: {topic}\n\n"
        f"I'll notify you when your episode is ready. "
        f"This usually takes 5-15 minutes."
    )

    asyncio.create_task(
        _run_pipeline_and_notify(chat_id, topic, context.bot)
    )


# ── Rating Callback ────────────────────────────────────────────────────────────

async def _handle_rating_callback(update: Update, context: Any) -> None:
    """
    Handle inline keyboard star rating button presses.

    Callback data format: "rate:<topic>:<rating>"
    The topic is URL-encoded to handle spaces and special chars.

    After recording the rating, asks for optional text feedback.
    """
    query = update.callback_query
    await query.answer()  # Dismiss the loading spinner on the button

    if not query.data or not query.data.startswith("rate:"):
        return

    chat_id = update.effective_chat.id
    if not _is_authorized(update):
        return

    # Parse callback data: "rate:<rating>:<topic>"
    # topic may contain colons so split on first two colons only
    parts = query.data.split(":", 2)
    if len(parts) != 3:
        logger.warning(f"Malformed rating callback data: {query.data}")
        return

    _, rating_str, topic = parts
    try:
        rating = int(rating_str)
    except ValueError:
        logger.warning(f"Non-integer rating in callback: {rating_str}")
        return

    logger.info(f"Rating received for '{topic}': {rating}/5 from chat_id={chat_id}")

    # Save the rating immediately (no text feedback yet)
    await record_feedback(topic, rating, None)

    stars_display = "★" * rating + "☆" * (5 - rating)
    await query.edit_message_text(
        f"Got it — {stars_display} for \"{topic}\"!\n\n"
        f"Any specific feedback? What worked well or could be better? "
        f"Reply with text, or /skip to pass."
    )

    # Mark that we're now waiting for text feedback
    _awaiting_text[chat_id] = topic


# ── Pipeline Runner ────────────────────────────────────────────────────────────

async def _run_pipeline_and_notify(chat_id: int, topic: str, bot: Bot) -> None:
    """
    Run the full GAVIN pipeline and send a follow-up Telegram message with the result.

    Runs as a background asyncio task. Sends a success or failure message when done.
    On success, schedules a 60-minute delayed feedback request.

    Args:
        chat_id: Telegram chat ID to send the follow-up message to.
        topic: The research topic/book title.
        bot: The Telegram Bot instance for sending messages.
    """
    logger.info(f"Background pipeline started for chat_id={chat_id}, topic='{topic}'")

    try:
        # Create the on_publish coroutine that will fire after successful publish
        feedback_coro = _send_feedback_request_after_delay(chat_id, topic, bot)

        result = await run_gavin_pipeline(topic, on_publish=feedback_coro)

        success_message = (
            f"✅ GAVIN has published your episode!\n\n"
            f"🎧 *{result['episode_title']}*\n\n"
            f"📊 {result['word_count']:,} words • {result['audio_duration_estimate']} of audio\n"
            f"⏱️ Completed in {result['total_time']}s\n\n"
            f"Your episode is now in your podcast feed."
        )

        if result.get("episode_url") and not result["episode_url"].startswith("episode"):
            success_message += f"\n\n🔗 {result['episode_url']}"

        success_message += "\n\n_I'll ask for your feedback in 60 minutes._"

        await bot.send_message(
            chat_id=chat_id,
            text=success_message,
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info(f"Success notification sent to chat_id={chat_id} for '{topic}'")

    except PipelineError as e:
        error_message = (
            f"❌ GAVIN hit a snag during *{e.step_name}*:\n\n"
            f"{e.error_summary[:300]}\n\n"
            f"Check the Railway logs for full details."
        )
        await bot.send_message(
            chat_id=chat_id,
            text=error_message,
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.error(
            f"Pipeline error for '{topic}' at step '{e.step_name}': {e.error_summary}"
        )

    except Exception as e:
        error_message = (
            f"❌ GAVIN encountered an unexpected error:\n\n"
            f"{str(e)[:300]}\n\n"
            f"Check the Railway logs for full details."
        )
        await bot.send_message(
            chat_id=chat_id,
            text=error_message,
        )
        logger.exception(f"Unexpected pipeline error for topic '{topic}': {e}")


async def _send_feedback_request_after_delay(
    chat_id: int, topic: str, bot: Bot
) -> None:
    """
    Wait 60 minutes then send an inline keyboard asking the user to rate the episode.

    This coroutine is passed to run_gavin_pipeline() as the on_publish callback
    and is scheduled as an asyncio task immediately after a successful publish.

    Args:
        chat_id: Telegram chat ID to send the feedback request to.
        topic: The episode topic — used to identify which episode is being rated
               and embedded in the callback data.
        bot: The Telegram Bot instance for sending messages.
    """
    logger.info(
        f"Feedback request scheduled for '{topic}' — firing in {_FEEDBACK_DELAY_SECONDS}s"
    )
    await asyncio.sleep(_FEEDBACK_DELAY_SECONDS)

    # Build inline keyboard: 5 star rating buttons
    # Callback format: "rate:<rating>:<topic>"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⭐ 1", callback_data=f"rate:1:{topic}"),
            InlineKeyboardButton("⭐ 2", callback_data=f"rate:2:{topic}"),
            InlineKeyboardButton("⭐ 3", callback_data=f"rate:3:{topic}"),
            InlineKeyboardButton("⭐ 4", callback_data=f"rate:4:{topic}"),
            InlineKeyboardButton("⭐ 5", callback_data=f"rate:5:{topic}"),
        ]
    ])

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"🎧 How many stars would you give this episode?\n\n"
                f"*{topic}*\n\n"
                f"Your rating helps improve future episodes:"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
        logger.info(f"Feedback request sent to chat_id={chat_id} for '{topic}'")
    except Exception as e:
        logger.error(
            f"Failed to send feedback request to chat_id={chat_id} for '{topic}': {e}"
        )


# ── Auth ───────────────────────────────────────────────────────────────────────

def _is_authorized(update: Update) -> bool:
    """
    Check whether the sending user is in the allowlist.

    Returns False and logs a warning for unauthorized users. Does NOT reply —
    silently ignores unknown users to avoid leaking bot existence.

    Args:
        update: The incoming Telegram Update.

    Returns:
        True if authorized, False otherwise.
    """
    if not update.effective_user:
        return False

    user_id = update.effective_user.id
    if user_id not in config.ALLOWED_TELEGRAM_USER_IDS:
        logger.warning(
            f"Ignoring message from unauthorized user ID: {user_id} "
            f"(@{update.effective_user.username})"
        )
        return False

    return True
