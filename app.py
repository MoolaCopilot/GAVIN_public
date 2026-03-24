"""
app.py — GAVIN FastAPI server.

Two endpoints:
  GET  /health          — health check (Railway monitors this)
  POST /webhook/telegram — receives Telegram webhook updates

On startup, automatically registers the Telegram webhook using the
RAILWAY_PUBLIC_DOMAIN environment variable set by Railway.
"""

import logging
import sys

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

import config
import bot as gavin_bot

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("gavin.app")

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="GAVIN",
    description="Generate Audio Via Intelligent Narration — research-to-podcast pipeline",
    version="1.0.0",
)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    """
    Run on server startup:
    1. Initialize the Telegram bot application.
    2. Register the Telegram webhook (if RAILWAY_PUBLIC_DOMAIN is set).
    """
    logger.info("🚀 GAVIN is online!")

    # Initialize the bot (sets up handlers, initializes Application)
    await gavin_bot.initialize_bot()

    # Register the Telegram webhook
    await _register_telegram_webhook()


async def _register_telegram_webhook() -> None:
    """
    Register this server's URL with Telegram as the webhook endpoint.

    Uses RAILWAY_PUBLIC_DOMAIN (set automatically by Railway) to construct
    the public URL. If not set (local dev), logs a warning with manual instructions.
    """
    if not config.RAILWAY_PUBLIC_DOMAIN:
        logger.warning(
            "⚠️  RAILWAY_PUBLIC_DOMAIN is not set — skipping automatic webhook registration.\n"
            "   To register manually, call:\n"
            f"   curl 'https://api.telegram.org/bot<TOKEN>/setWebhook"
            f"?url=https://<YOUR_DOMAIN>/webhook/telegram"
            f"&secret_token={config.TELEGRAM_WEBHOOK_SECRET}'"
        )
        return

    webhook_url = (
        f"https://{config.RAILWAY_PUBLIC_DOMAIN}/webhook/telegram"
    )

    register_url = (
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/setWebhook"
    )

    payload = {
        "url": webhook_url,
        "secret_token": config.TELEGRAM_WEBHOOK_SECRET,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(register_url, json=payload)
            result = response.json()

        if result.get("ok"):
            logger.info(
                f"✅ Telegram webhook registered: {webhook_url}"
            )
        else:
            logger.error(
                f"❌ Failed to register Telegram webhook: {result}"
            )
    except Exception as e:
        logger.error(f"❌ Error registering Telegram webhook: {e}")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check() -> JSONResponse:
    """
    Health check endpoint. Railway polls this to confirm the service is running.

    Returns:
        JSON with status and service name.
    """
    return JSONResponse({"status": "ok", "service": "GAVIN"})


@app.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> JSONResponse:
    """
    Receive and process Telegram webhook updates.

    Verifies the secret token header to ensure the request is genuinely from
    Telegram (prevents unauthorized callers from triggering the pipeline).

    Args:
        request: The incoming FastAPI request.
        x_telegram_bot_api_secret_token: Telegram passes our secret in this header.

    Returns:
        200 OK immediately. The pipeline runs in the background.

    Raises:
        HTTPException 403: If the secret token is missing or wrong.
    """
    # Verify secret token — Telegram sends this in X-Telegram-Bot-Api-Secret-Token
    if x_telegram_bot_api_secret_token != config.TELEGRAM_WEBHOOK_SECRET:
        logger.warning(
            f"Rejected webhook request with invalid secret token: "
            f"'{x_telegram_bot_api_secret_token}'"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid secret token.",
        )

    update_data = await request.json()
    logger.debug(f"Received Telegram update: {update_data}")

    # Process the update via the bot — pipeline runs as a background task
    await gavin_bot.process_update(update_data)

    # Return 200 immediately — Telegram will retry if we don't respond quickly
    return JSONResponse({"ok": True})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=config.PORT,
        log_level="info",
    )
