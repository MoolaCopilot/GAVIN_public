"""
config.py — GAVIN configuration loader.

Loads all required environment variables at startup and fails fast with a clear
error if anything is missing. Import `config` anywhere you need a setting.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("gavin.config")


def _require(name: str) -> str:
    """Return the value of an env var or raise a clear RuntimeError."""
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"❌ Missing required environment variable: {name}\n"
            f"   Copy .env.example to .env and fill in all values."
        )
    return val


# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = _require("TELEGRAM_BOT_TOKEN")
TELEGRAM_WEBHOOK_SECRET: str = _require("TELEGRAM_WEBHOOK_SECRET")

# Comma-separated list of Telegram user IDs allowed to use the bot.
# Only these user IDs can trigger the pipeline — everyone else is silently ignored.
_raw_ids: str = _require("ALLOWED_TELEGRAM_USER_IDS")
ALLOWED_TELEGRAM_USER_IDS: set[int] = {
    int(uid.strip()) for uid in _raw_ids.split(",") if uid.strip()
}

# ── Anthropic / Claude ────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")

# ── ElevenLabs ────────────────────────────────────────────────────────────────
ELEVENLABS_API_KEY: str = _require("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID: str = _require("ELEVENLABS_VOICE_ID")

# ── Transistor.fm ─────────────────────────────────────────────────────────────
HELLOAUDIO_API_KEY: str = _require("TRANSISTOR_API_KEY")
HELLOAUDIO_PODCAST_ID: str = _require("TRANSISTOR_PODCAST_ID")

# ── Server ────────────────────────────────────────────────────────────────────
PORT: int = int(os.getenv("PORT", "8080"))

# Railway automatically sets RAILWAY_PUBLIC_DOMAIN to the public URL of the service.
# Used to register the Telegram webhook on startup.
RAILWAY_PUBLIC_DOMAIN: str | None = os.getenv("RAILWAY_PUBLIC_DOMAIN")

logger.info("✅ Configuration loaded successfully.")
