"""
audio.py — ElevenLabs text-to-speech integration for GAVIN.

Converts research text to MP3 audio. Handles long-form content by splitting
into chunks at paragraph breaks when the text exceeds ElevenLabs' per-request
character limit, then concatenates the chunks with pydub.

Requires ffmpeg to be installed (available on Railway via railway.toml nixPkgs).
"""

import asyncio
import logging
import os
import subprocess
import tempfile
import time

import httpx

import config

logger = logging.getLogger("gavin.audio")

# ElevenLabs API base URL
ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"

# Best model for long-form narration content
TTS_MODEL = "eleven_multilingual_v2"

# ElevenLabs character limit per request (actual limit is ~5000; we stay conservative)
CHUNK_SIZE_LIMIT = 4800

# Retry settings for rate limiting
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds


def _split_text_into_chunks(text: str, max_chars: int = CHUNK_SIZE_LIMIT) -> list[str]:
    """
    Split text into chunks at paragraph breaks, keeping each chunk under max_chars.

    Splits on double newlines (paragraph breaks) to avoid cutting sentences mid-flow.
    If a single paragraph exceeds max_chars, it falls back to splitting at sentence
    boundaries (periods followed by a space).

    Args:
        text: The full research text to split.
        max_chars: Maximum characters per chunk.

    Returns:
        List of text chunks, each under max_chars characters.
    """
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current_chunk = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        # If a single paragraph exceeds the limit, split it further at sentences
        if len(paragraph) > max_chars:
            sentences = paragraph.replace(". ", ".|").split("|")
            for sentence in sentences:
                if len(current_chunk) + len(sentence) + 2 <= max_chars:
                    current_chunk = (current_chunk + " " + sentence).strip()
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = sentence
        elif len(current_chunk) + len(paragraph) + 2 <= max_chars:
            current_chunk = (current_chunk + "\n\n" + paragraph).strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = paragraph

    if current_chunk:
        chunks.append(current_chunk)

    logger.info(f"Split text into {len(chunks)} chunks (total {len(text):,} chars).")
    return chunks


async def _convert_chunk_to_mp3(
    client: httpx.AsyncClient,
    text: str,
    chunk_index: int,
    total_chunks: int,
) -> bytes:
    """
    Convert a single text chunk to MP3 bytes via the ElevenLabs API.

    Retries on rate limiting with exponential backoff.

    Args:
        client: Shared httpx async client.
        text: Text chunk to convert.
        chunk_index: 1-based index of this chunk (for logging).
        total_chunks: Total number of chunks (for logging).

    Returns:
        Raw MP3 bytes for this chunk.

    Raises:
        RuntimeError: If the API call fails after all retries.
    """
    url = f"{ELEVENLABS_API_BASE}/text-to-speech/{config.ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": TTS_MODEL,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(
            f"TTS chunk {chunk_index}/{total_chunks} — attempt {attempt} "
            f"({len(text):,} chars)"
        )
        try:
            response = await client.post(url, headers=headers, json=payload, timeout=120.0)

            if response.status_code == 200:
                logger.info(
                    f"TTS chunk {chunk_index}/{total_chunks} — success "
                    f"({len(response.content):,} bytes)"
                )
                return response.content

            elif response.status_code == 429:
                # Rate limited — wait and retry
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"TTS chunk {chunk_index}/{total_chunks} — rate limited, "
                    f"retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)

            else:
                error_body = response.text[:500]
                raise RuntimeError(
                    f"ElevenLabs API error (status {response.status_code}) "
                    f"for chunk {chunk_index}: {error_body}"
                )

        except httpx.TimeoutException:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"TTS chunk {chunk_index}/{total_chunks} — timeout, "
                    f"retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                raise RuntimeError(
                    f"ElevenLabs API timed out for chunk {chunk_index} "
                    f"after {MAX_RETRIES} attempts."
                )

    raise RuntimeError(
        f"ElevenLabs API rate limit exceeded for chunk {chunk_index} "
        f"after {MAX_RETRIES} attempts."
    )


async def text_to_speech(text: str, title: str) -> str:
    """
    Convert text to MP3. Returns path to the MP3 file.

    Handles long-form content by splitting into chunks, converting each separately,
    and concatenating the results using pydub. The resulting MP3 is saved to a
    temporary file.

    Args:
        text: The full research text to convert to audio.
        title: Episode title (used in the temp file name for easy identification).

    Returns:
        Absolute path to the generated MP3 file.

    Raises:
        RuntimeError: If TTS conversion fails at any step.
    """
    total_chars = len(text)
    logger.info(
        f"Starting TTS conversion for '{title}' "
        f"({total_chars:,} chars, ~{total_chars // 15:,} words)"
    )
    start_time = time.time()

    chunks = _split_text_into_chunks(text)
    total_chunks = len(chunks)

    # Create a safe filename from the title
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:50]
    temp_dir = tempfile.gettempdir()

    async with httpx.AsyncClient() as client:
        if total_chunks == 1:
            # Single chunk — convert directly
            mp3_bytes = await _convert_chunk_to_mp3(client, chunks[0], 1, 1)
            output_path = os.path.join(temp_dir, f"gavin_{safe_title}.mp3")
            with open(output_path, "wb") as f:
                f.write(mp3_bytes)

        else:
            # Multiple chunks — convert each and concatenate
            chunk_paths: list[str] = []

            for i, chunk in enumerate(chunks, start=1):
                mp3_bytes = await _convert_chunk_to_mp3(client, chunk, i, total_chunks)
                chunk_path = os.path.join(temp_dir, f"gavin_{safe_title}_chunk{i}.mp3")
                with open(chunk_path, "wb") as f:
                    f.write(mp3_bytes)
                chunk_paths.append(chunk_path)

            logger.info(f"Concatenating {total_chunks} audio chunks with ffmpeg...")
            output_path = os.path.join(temp_dir, f"gavin_{safe_title}.mp3")

            # Write an ffmpeg concat list file
            concat_list_path = os.path.join(temp_dir, f"gavin_{safe_title}_concat.txt")
            with open(concat_list_path, "w") as f:
                for path in chunk_paths:
                    # ffmpeg concat list requires escaped paths
                    escaped = path.replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")

            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", concat_list_path,
                    "-acodec", "copy",
                    output_path,
                ],
                capture_output=True,
                text=True,
            )
            os.remove(concat_list_path)

            if result.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg concatenation failed (exit {result.returncode}): "
                    f"{result.stderr[-500:]}"
                )
            logger.info(f"Concatenation complete.")

            # Clean up chunk files
            for path in chunk_paths:
                try:
                    os.remove(path)
                except OSError:
                    pass

    elapsed = time.time() - start_time
    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(
        f"TTS complete in {elapsed:.1f}s | "
        f"File: {output_path} | "
        f"Size: {file_size_mb:.1f} MB | "
        f"Total chars processed: {total_chars:,}"
    )

    return output_path
