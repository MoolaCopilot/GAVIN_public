"""
research.py — Two-pass Claude API integration for GAVIN.

Pass 1 (gather_research): Claude Opus 4.6 + web search — pure information
gathering. Outputs a structured research brief. Never heard by listeners.

Pass 2 (write_episode): Claude Opus 4.6, no web search — pure writing.
Receives the research brief as context and produces the final episode script.
"""

import asyncio
import logging
import time
from typing import Any

import anthropic

import config
from system_prompt import (
    RESEARCH_SYSTEM_PROMPT,
    GAVIN_SYSTEM_PROMPT,
    build_research_message,
    build_writing_message,
)

logger = logging.getLogger("gavin.research")

# Pass 1 — Sonnet for research gathering (faster, cheaper, higher rate limits)
RESEARCH_MODEL = "claude-sonnet-4-6"
RESEARCH_MAX_TOKENS = 8000

# Pass 2 — Opus for episode writing (best quality narration)
WRITING_MODEL = "claude-opus-4-6"
WRITING_MAX_TOKENS = 16000

WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "web_search_20250305",
    "name": "web_search",
}


def _extract_post_search_text(content: list[Any]) -> str:
    """
    Extract text written after the first web search call, skipping any preamble.

    Skips text blocks that appear before the first tool_use block (preamble like
    "I'll research this now..."). Keeps all text blocks after the first search.
    If no search was used, returns all text.
    """
    first_tool_index = next(
        (i for i, block in enumerate(content)
         if hasattr(block, "type") and block.type == "tool_use"),
        None,
    )

    if first_tool_index is None:
        text_parts = [
            block.text for block in content
            if hasattr(block, "type") and block.type == "text"
        ]
    else:
        text_parts = [
            block.text for block in content[first_tool_index:]
            if hasattr(block, "type") and block.type == "text"
        ]

    return "\n\n".join(text_parts).strip()


def _extract_all_text(content: list[Any]) -> str:
    """Extract and concatenate all text blocks (used for Pass 2, no search)."""
    text_parts = [
        block.text for block in content
        if hasattr(block, "type") and block.type == "text"
    ]
    return "\n\n".join(text_parts).strip()


async def _call_claude(
    model: str,
    system: str,
    user_message: str,
    max_tokens: int,
    tools: list[dict] | None = None,
    pass_label: str = "",
    memory_prefix: str = "",
) -> Any:
    """
    Make a Claude API call with retry logic for rate limits.

    Token-based rate limits (30k input tokens/min) need a full 65-second wait
    to reset — far longer than the SDK's default 0.4s / 0.8s backoff.
    We disable SDK retries and handle 429s ourselves with a proper window wait.

    Args:
        system: System prompt.
        user_message: User message content.
        max_tokens: Maximum tokens for the response.
        tools: Optional list of tool definitions (e.g. web search).
        pass_label: Label for log messages (e.g. "Pass 1", "Pass 2").

    Returns:
        The raw Anthropic API response object.

    Raises:
        RuntimeError: On non-rate-limit API errors, or after all retries exhausted.
    """
    # Disable SDK retries — we handle rate limits ourselves with proper wait times
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=0)

    # Prepend memory context to system prompt if provided
    full_system = f"{memory_prefix}{system}" if memory_prefix else system

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": full_system,
        "messages": [{"role": "user", "content": user_message}],
    }
    if tools:
        kwargs["tools"] = tools

    rate_limit_retries = 3
    rate_limit_wait = 65  # seconds — enough for the 1-minute token window to reset

    for attempt in range(1, rate_limit_retries + 1):
        try:
            return client.messages.create(**kwargs)

        except anthropic.RateLimitError as e:
            if attempt < rate_limit_retries:
                logger.warning(
                    f"[{pass_label}] Rate limit hit (attempt {attempt}/{rate_limit_retries}). "
                    f"Waiting {rate_limit_wait}s for token window to reset..."
                )
                await asyncio.sleep(rate_limit_wait)
            else:
                raise RuntimeError(
                    f"Anthropic API rate limit exceeded after {rate_limit_retries} attempts: {e}"
                ) from e

        except anthropic.APIConnectionError as e:
            raise RuntimeError(f"Failed to connect to Anthropic API: {e}") from e
        except anthropic.APIStatusError as e:
            raise RuntimeError(
                f"Anthropic API error (status {e.status_code}): {e.message}"
            ) from e


async def gather_research(topic: str, memory_context: str = "") -> str:
    """
    Pass 1 — Research gathering.

    Claude Sonnet 4.6 with web search enabled. Plans targeted research questions,
    executes multiple searches, and outputs a comprehensive research brief.
    No episode writing — pure information gathering.

    Args:
        topic: The book title or topic to research.
        memory_context: Optional memory injection block (company context + lessons).

    Returns:
        A structured research brief (can contain markdown — internal only).

    Raises:
        RuntimeError: If the API call fails or returns no content.
    """
    logger.info(f"[Pass 1] Starting research gathering for: '{topic}'")
    start_time = time.time()

    response = await _call_claude(
        model=RESEARCH_MODEL,
        system=RESEARCH_SYSTEM_PROMPT,
        user_message=build_research_message(topic),
        max_tokens=RESEARCH_MAX_TOKENS,
        tools=[WEB_SEARCH_TOOL],
        pass_label="Pass 1",
        memory_prefix=memory_context,
    )

    elapsed = time.time() - start_time
    usage = response.usage
    logger.info(
        f"[Pass 1] Research complete in {elapsed:.1f}s | "
        f"Input: {usage.input_tokens} tokens | "
        f"Output: {usage.output_tokens} tokens | "
        f"Stop: {response.stop_reason}"
    )

    brief = _extract_post_search_text(response.content)

    if not brief:
        raise RuntimeError(
            f"[Pass 1] Claude returned no research content for '{topic}'. "
            f"Stop reason: {response.stop_reason}."
        )

    word_count = len(brief.split())
    logger.info(f"[Pass 1] Research brief: {word_count:,} words.")

    return brief


async def write_episode(topic: str, research_brief: str, memory_context: str = "") -> str:
    """
    Pass 2 — Episode writing.

    Claude Opus 4.6 with web search DISABLED. Receives the full research brief
    from Pass 1 as context and writes the complete episode script. No searching,
    no tool calls — pure focused writing.

    Args:
        topic: The original topic/book title.
        research_brief: The structured research brief from gather_research().
        memory_context: Optional memory injection block (company context + lessons).

    Returns:
        The complete episode script as clean plain text, ready for TTS.

    Raises:
        RuntimeError: If the API call fails or returns no content.
    """
    logger.info(f"[Pass 2] Starting episode writing for: '{topic}'")
    start_time = time.time()

    # No tools — web search is intentionally disabled in the writing pass
    response = await _call_claude(
        model=WRITING_MODEL,
        system=GAVIN_SYSTEM_PROMPT,
        user_message=build_writing_message(topic, research_brief),
        max_tokens=WRITING_MAX_TOKENS,
        tools=None,
        pass_label="Pass 2",
        memory_prefix=memory_context,
    )

    elapsed = time.time() - start_time
    usage = response.usage
    logger.info(
        f"[Pass 2] Writing complete in {elapsed:.1f}s | "
        f"Input: {usage.input_tokens} tokens | "
        f"Output: {usage.output_tokens} tokens | "
        f"Stop: {response.stop_reason}"
    )

    episode = _extract_all_text(response.content)

    if not episode:
        raise RuntimeError(
            f"[Pass 2] Claude returned no episode content for '{topic}'. "
            f"Stop reason: {response.stop_reason}."
        )

    word_count = len(episode.split())
    logger.info(f"[Pass 2] Episode script: {word_count:,} words.")

    return episode
