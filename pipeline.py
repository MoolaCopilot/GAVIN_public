"""
pipeline.py — GAVIN's full orchestration pipeline.

Two-pass research architecture:
  Pass 1: gather_research()  — Claude + web search, pure information gathering
  Pass 2: write_episode()    — Claude no search, pure writing from research brief

Then: TTS → publish.
"""

import asyncio
import logging
import os
import re
import time

from research import gather_research, write_episode
from audio import text_to_speech
from publisher import publish_episode
from memory import (
    load_episode_memory,
    load_company_memory,
    build_memory_injection,
    log_episode,
)

logger = logging.getLogger("gavin.pipeline")


def _clean_text_for_tts(text: str) -> str:
    """
    Strip any remaining markdown artifacts and normalize whitespace for TTS.

    Pass 2 is instructed not to use markdown, but this catches any stray
    formatting before the text reaches ElevenLabs.
    """
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*{1,3}(.*?)\*{1,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.*?)_{1,3}", r"\1", text)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"`{1,3}(.*?)`{1,3}", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _estimate_audio_duration(word_count: int) -> str:
    """Estimate audio duration at ~130 words per minute."""
    minutes = round(word_count / 130)
    if minutes < 2:
        return "~1 minute"
    return f"~{minutes} minutes"


async def run_gavin_pipeline(
    topic: str,
    on_publish=None,
) -> dict:
    """
    Full GAVIN pipeline: topic → research brief → episode script → audio → publish.

    Steps:
      1. Pass 1: gather_research()  — web search, build research brief
      2. Pass 2: write_episode()    — write episode from brief, no searching
      3. Clean text for TTS
      4. ElevenLabs TTS → MP3
      5. Transistor.fm publish
      6. Cleanup

    Args:
        topic: The book title or research topic from the Telegram message.
        on_publish: Optional async coroutine to await after a successful publish.
                    Used by bot.py to schedule the 60-minute feedback request.

    Returns:
        Dict with: topic, episode_title, episode_url, total_time,
        word_count, audio_duration_estimate, status.

    Raises:
        PipelineError: With step_name set so bot.py can report which step failed.
    """
    logger.info("=" * 60)
    logger.info(f"GAVIN pipeline starting for topic: '{topic}'")
    logger.info("=" * 60)

    pipeline_start = time.time()
    mp3_path: str | None = None

    # ── Load memory context ───────────────────────────────────────────────────
    episode_memory = load_episode_memory()
    company_memory = load_company_memory()
    memory_context = build_memory_injection(episode_memory, company_memory)
    if memory_context:
        logger.info("Memory context loaded and will be injected into both passes")
    else:
        logger.info("No memory context found — running without injection")

    # ── Step 1: Research gathering (Pass 1) ───────────────────────────────────
    step_name = "research (Pass 1 — gathering)"
    logger.info(f"[1/4] RESEARCH PASS 1 — Claude Sonnet 4.6 + web search")
    step_start = time.time()
    try:
        research_brief = await gather_research(topic, memory_context=memory_context)
    except Exception as e:
        raise _PipelineError(step_name, str(e)) from e

    logger.info(f"[1/4] RESEARCH PASS 1 complete in {time.time() - step_start:.1f}s")

    # ── Step 2: Episode writing (Pass 2) ──────────────────────────────────────
    step_name = "research (Pass 2 — writing)"
    logger.info(f"[2/4] RESEARCH PASS 2 — Claude Opus 4.6, writing only (no search)")
    step_start = time.time()
    try:
        raw_episode = await write_episode(topic, research_brief, memory_context=memory_context)
    except Exception as e:
        raise _PipelineError(step_name, str(e)) from e

    logger.info(f"[2/4] RESEARCH PASS 2 complete in {time.time() - step_start:.1f}s")

    # ── Clean text ────────────────────────────────────────────────────────────
    clean_text = _clean_text_for_tts(raw_episode)
    word_count = len(clean_text.split())
    duration_estimate = _estimate_audio_duration(word_count)
    logger.info(
        f"Episode ready: {word_count:,} words, {len(clean_text):,} chars, "
        f"estimated {duration_estimate}"
    )

    # ── Step 3: Text-to-Speech ────────────────────────────────────────────────
    step_name = "audio"
    logger.info(f"[3/4] AUDIO — ElevenLabs TTS")
    step_start = time.time()
    try:
        mp3_path = await text_to_speech(clean_text, topic)
    except Exception as e:
        raise _PipelineError(step_name, str(e)) from e

    file_size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
    logger.info(
        f"[3/4] AUDIO complete in {time.time() - step_start:.1f}s | "
        f"{file_size_mb:.1f} MB"
    )

    # ── Step 4: Publish ───────────────────────────────────────────────────────
    step_name = "publish"
    logger.info(f"[4/4] PUBLISH — Transistor.fm")
    description = clean_text[:500].rsplit(" ", 1)[0] + "..."
    step_start = time.time()
    try:
        episode_url = await publish_episode(mp3_path, topic, description)
    except Exception as e:
        raise _PipelineError(step_name, str(e)) from e

    logger.info(f"[4/4] PUBLISH complete in {time.time() - step_start:.1f}s | {episode_url}")

    # ── Log episode to memory ─────────────────────────────────────────────────
    log_episode(topic, word_count, duration_estimate)

    # ── Schedule feedback request ─────────────────────────────────────────────
    if on_publish is not None:
        asyncio.create_task(on_publish)

    # ── Cleanup ───────────────────────────────────────────────────────────────
    if mp3_path and os.path.exists(mp3_path):
        try:
            os.remove(mp3_path)
            logger.info(f"Cleaned up temp file: {mp3_path}")
        except OSError as e:
            logger.warning(f"Could not remove temp file {mp3_path}: {e}")

    total_time = time.time() - pipeline_start
    logger.info("=" * 60)
    logger.info(f"GAVIN pipeline COMPLETE in {total_time:.1f}s | '{topic}' → {episode_url}")
    logger.info("=" * 60)

    return {
        "topic": topic,
        "episode_title": topic,
        "episode_url": episode_url,
        "total_time": round(total_time, 1),
        "word_count": word_count,
        "audio_duration_estimate": duration_estimate,
        "status": "success",
    }


class _PipelineError(Exception):
    """Wraps a pipeline step failure with the step name for bot.py error reporting."""

    def __init__(self, step_name: str, message: str) -> None:
        self.step_name = step_name
        self.error_summary = message
        super().__init__(f"Pipeline failed at step '{step_name}': {message}")


PipelineError = _PipelineError
