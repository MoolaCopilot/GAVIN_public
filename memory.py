"""
memory.py — GAVIN's two-layer persistent memory system.

Episode Memory (gavin_memory.json on Railway volume /data):
  - A running log of all published episodes with optional star ratings + text feedback
  - Claude Haiku synthesizes all rated feedback into an evolving "lessons learned" doc
  - The lessons doc is prepended to both Pass 1 and Pass 2 system prompts on every run

Company Memory (company_memory.md):
  - Baseline: COMPANY_MEMORY.md in the repo (edit via GitHub)
  - Override: /data/company_memory.md on Railway volume (edit via /memory Telegram command)
  - Volume file takes priority — allows instant updates without a redeploy
  - Injected into both system prompts on every run

Both are combined into a single memory injection block prepended to all prompts.
"""

import json
import logging
import os
import time
from typing import Any

import anthropic

import config

logger = logging.getLogger("gavin.memory")

# ── Storage paths ─────────────────────────────────────────────────────────────

# Railway persistent volume (production). Falls back to local for dev.
_DATA_DIR = "/data" if os.path.isdir("/data") else "."

EPISODE_MEMORY_FILE = os.path.join(_DATA_DIR, "gavin_memory.json")
COMPANY_MEMORY_OVERRIDE = os.path.join(_DATA_DIR, "company_memory.md")
COMPANY_MEMORY_REPO = os.path.join(os.path.dirname(__file__), "COMPANY_MEMORY.md")

# Claude Haiku for cheap lesson synthesis
_SYNTHESIS_MODEL = "claude-haiku-4-5-20251001"

# ── Episode Memory ─────────────────────────────────────────────────────────────


def load_episode_memory() -> dict[str, Any]:
    """
    Load the episode memory JSON from disk.

    Returns a dict with:
      - "lessons": str — synthesized lessons doc (empty string if no feedback yet)
      - "episodes": list — all episode records (rated + unrated)
    """
    if not os.path.exists(EPISODE_MEMORY_FILE):
        return {"lessons": "", "episodes": []}

    try:
        with open(EPISODE_MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Ensure expected keys exist
        data.setdefault("lessons", "")
        data.setdefault("episodes", [])
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not load episode memory from {EPISODE_MEMORY_FILE}: {e}")
        return {"lessons": "", "episodes": []}


def save_episode_memory(memory: dict[str, Any]) -> None:
    """Persist the episode memory dict to disk."""
    try:
        with open(EPISODE_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2, ensure_ascii=False)
        logger.debug(f"Episode memory saved to {EPISODE_MEMORY_FILE}")
    except OSError as e:
        logger.error(f"Could not save episode memory to {EPISODE_MEMORY_FILE}: {e}")


def log_episode(topic: str, word_count: int, duration_estimate: str) -> None:
    """
    Add a new unrated episode entry to the memory log.

    Called immediately after a successful publish so the episode exists in
    memory before the 60-minute feedback request fires.

    Args:
        topic: The episode topic/title.
        word_count: Word count of the final script.
        duration_estimate: Human-readable duration string (e.g. "~22 minutes").
    """
    memory = load_episode_memory()
    memory["episodes"].append({
        "topic": topic,
        "word_count": word_count,
        "duration_estimate": duration_estimate,
        "published_at": time.time(),
        "rating": None,
        "feedback": None,
    })
    save_episode_memory(memory)
    logger.info(f"Episode logged to memory: '{topic}'")


async def record_feedback(topic: str, rating: int, feedback_text: str | None) -> None:
    """
    Record a star rating and optional text feedback for the most recent episode
    matching the given topic, then re-synthesize the lessons doc.

    Args:
        topic: The episode topic used to find the right episode entry.
        rating: Integer 1–5.
        feedback_text: Optional free-text feedback from the user.
    """
    memory = load_episode_memory()

    # Find the most recent unrated episode for this topic
    updated = False
    for ep in reversed(memory["episodes"]):
        if ep["topic"] == topic and ep["rating"] is None:
            ep["rating"] = rating
            ep["feedback"] = feedback_text
            ep["rated_at"] = time.time()
            updated = True
            break

    if not updated:
        # Fallback: update the most recent episode with this topic (even if already rated)
        for ep in reversed(memory["episodes"]):
            if ep["topic"] == topic:
                ep["rating"] = rating
                ep["feedback"] = feedback_text
                ep["rated_at"] = time.time()
                updated = True
                break

    if not updated:
        logger.warning(f"No episode found in memory for topic: '{topic}'")
        return

    # Re-synthesize lessons from all rated episodes
    rated = [ep for ep in memory["episodes"] if ep["rating"] is not None]
    if rated:
        memory["lessons"] = await _synthesize_lessons(rated)
        logger.info(f"Lessons re-synthesized from {len(rated)} rated episode(s)")

    save_episode_memory(memory)


async def _synthesize_lessons(rated_episodes: list[dict]) -> str:
    """
    Use Claude Haiku to synthesize all rated episode feedback into a concise
    lessons-learned document.

    This is called after every new rating so the lessons doc stays current.
    The output is injected into both system prompts on every pipeline run.

    Args:
        rated_episodes: List of episode dicts that have a rating (and optional feedback).

    Returns:
        A 300–400 word lessons document as plain text.
    """
    # Build the episode history for Haiku to analyze
    history_lines = []
    for ep in rated_episodes:
        stars = "★" * ep["rating"] + "☆" * (5 - ep["rating"])
        line = f"• {ep['topic']} — {stars} ({ep['rating']}/5)"
        if ep.get("feedback"):
            line += f"\n  Feedback: {ep['feedback']}"
        history_lines.append(line)

    history_text = "\n".join(history_lines)

    prompt = f"""You are analyzing feedback for GAVIN, an automated podcast research system.

Below is the complete episode feedback history, showing every rated episode with star ratings and optional listener feedback:

{history_text}

Based on this feedback, synthesize a concise "lessons learned" document (300-400 words) that will be prepended to GAVIN's research and writing prompts on every future run.

The document should:
1. Identify clear patterns in what listeners loved (high-rated episodes) vs. what fell flat
2. Extract 4-6 specific, actionable guidance points for future research and writing
3. Note any specific topics, formats, or approaches that worked especially well or poorly
4. Be written as direct instructions to the research/writing system (e.g. "Listeners respond well to...", "Avoid...")
5. Be honest and specific — generic advice is useless

Write in plain text only. No markdown, no bullet points, no headers. Just flowing prose that reads like a memo from a thoughtful editor."""

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=1)
        response = client.messages.create(
            model=_SYNTHESIS_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        lessons = response.content[0].text.strip()
        logger.info(f"Lessons synthesized: {len(lessons.split())} words")
        return lessons
    except Exception as e:
        logger.error(f"Failed to synthesize lessons with Haiku: {e}")
        return ""


# ── Company Memory ─────────────────────────────────────────────────────────────


def load_company_memory() -> str:
    """
    Load the company memory content.

    Priority order:
      1. /data/company_memory.md (Railway volume — set via /memory Telegram command)
      2. COMPANY_MEMORY.md in the repo directory (edit via GitHub)

    Returns:
        The company memory content as a string, or empty string if neither exists.
    """
    # Check volume override first
    if os.path.exists(COMPANY_MEMORY_OVERRIDE):
        try:
            with open(COMPANY_MEMORY_OVERRIDE, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                logger.debug("Company memory loaded from Railway volume override")
                return content
        except OSError as e:
            logger.warning(f"Could not read company memory override: {e}")

    # Fall back to repo file
    if os.path.exists(COMPANY_MEMORY_REPO):
        try:
            with open(COMPANY_MEMORY_REPO, "r", encoding="utf-8") as f:
                content = f.read().strip()
            logger.debug("Company memory loaded from repo COMPANY_MEMORY.md")
            return content
        except OSError as e:
            logger.warning(f"Could not read repo company memory: {e}")

    logger.warning("No company memory found (checked volume and repo)")
    return ""


def save_company_memory(content: str) -> bool:
    """
    Save updated company memory to the Railway volume override path.

    This is called by the /memory Telegram command. The volume file takes
    priority over the repo file so this takes effect immediately without
    a redeploy.

    Args:
        content: The new company memory content.

    Returns:
        True if saved successfully, False otherwise.
    """
    try:
        with open(COMPANY_MEMORY_OVERRIDE, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"Company memory saved to {COMPANY_MEMORY_OVERRIDE}")
        return True
    except OSError as e:
        logger.error(f"Could not save company memory to {COMPANY_MEMORY_OVERRIDE}: {e}")
        return False


# ── Memory Injection Block ─────────────────────────────────────────────────────


def build_memory_injection(episode_memory: dict[str, Any], company_memory: str) -> str:
    """
    Build the memory injection block that gets prepended to both system prompts.

    The block contains:
      1. Company context (always present if COMPANY_MEMORY.md exists)
      2. Episode lessons (only present after at least one episode has been rated)

    Args:
        episode_memory: Dict from load_episode_memory().
        company_memory: String from load_company_memory().

    Returns:
        A formatted string ready to prepend to a system prompt, or empty string
        if there is nothing to inject.
    """
    parts = []

    if company_memory:
        parts.append(f"=== COMPANY CONTEXT ===\n\n{company_memory}")

    lessons = episode_memory.get("lessons", "").strip()
    if lessons:
        parts.append(f"=== LESSONS FROM PAST EPISODES ===\n\n{lessons}")

    if not parts:
        return ""

    injection = "\n\n".join(parts)
    return f"{injection}\n\n=== END RUNTIME MEMORY ===\n\n"
