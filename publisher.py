"""
publisher.py — Transistor.fm API integration for GAVIN.

Uploads the generated MP3 to Transistor.fm and publishes it as a podcast episode
in your private feed. Transistor has a proper REST API that supports programmatic
episode creation and audio upload.

API docs: https://developers.transistor.fm/

Correct three-step Transistor upload flow:
  1. GET /v1/episodes/authorize_upload?filename=X  → signed S3 URL + final audio_url
  2. PUT the MP3 to the signed S3 URL
  3. POST /v1/episodes with audio_url + show_id + title → episode is created and published
"""

import logging
import os
import time

import httpx

import config

logger = logging.getLogger("gavin.publisher")

TRANSISTOR_API_BASE = "https://api.transistor.fm/v1"

# Timeout for the audio file upload (MP3 files can be 20–100 MB)
UPLOAD_TIMEOUT_SECONDS = 300.0


def _transistor_headers() -> dict[str, str]:
    """Return the auth headers required by all Transistor API calls."""
    return {
        "x-api-key": config.HELLOAUDIO_API_KEY,
        "Content-Type": "application/json",
    }


async def publish_episode(mp3_path: str, title: str, description: str) -> str:
    """
    Upload MP3 and publish as podcast episode. Returns episode share URL.

    Correct Transistor.fm upload flow:
      1. GET /episodes/authorize_upload?filename=X  — get signed S3 URL + audio_url
      2. PUT the MP3 to the signed S3 URL
      3. POST /episodes with audio_url → create and publish the episode

    Args:
        mp3_path: Absolute path to the generated MP3 file.
        title: Episode title (from the research topic).
        description: Short episode description (~500 chars of research text).

    Returns:
        The Transistor episode share URL (e.g., https://share.transistor.fm/e/abc123).

    Raises:
        RuntimeError: If any step of the upload flow fails.
    """
    logger.info(f"Publishing episode '{title}' to Transistor.fm...")
    start_time = time.time()

    file_size_mb = os.path.getsize(mp3_path) / (1024 * 1024)
    filename = os.path.basename(mp3_path)
    logger.info(f"Audio file: {filename} ({file_size_mb:.1f} MB)")

    async with httpx.AsyncClient(timeout=30.0) as client:

        # ── Step 1: Get signed S3 upload URL ─────────────────────────────────
        # This endpoint returns a pre-signed S3 URL + the final audio_url
        # that we'll attach to the episode after upload.
        logger.info("Step 1/3 — Requesting signed audio upload authorization...")
        auth_response = await client.get(
            f"{TRANSISTOR_API_BASE}/episodes/authorize_upload",
            headers=_transistor_headers(),
            params={"filename": filename},
        )

        if auth_response.status_code != 200:
            raise RuntimeError(
                f"Transistor authorize_upload failed (status {auth_response.status_code}): "
                f"{auth_response.text[:500]}"
            )

        auth_data = auth_response.json()
        signed_upload_url = auth_data["data"]["attributes"]["upload_url"]
        audio_url = auth_data["data"]["attributes"]["audio_url"]
        logger.info("Step 1/3 — Got signed S3 upload URL.")

    # ── Step 2: PUT the MP3 file to the signed S3 URL ────────────────────────
    logger.info(f"Step 2/3 — Uploading {file_size_mb:.1f} MB to S3...")
    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS) as upload_client:
        with open(mp3_path, "rb") as mp3_file:
            mp3_bytes = mp3_file.read()

        put_response = await upload_client.put(
            signed_upload_url,
            content=mp3_bytes,
            headers={"Content-Type": "audio/mpeg"},
        )

        if put_response.status_code not in (200, 201, 204):
            raise RuntimeError(
                f"S3 audio upload failed (status {put_response.status_code}): "
                f"{put_response.text[:300]}"
            )

    logger.info("Step 2/3 — Audio uploaded to S3 successfully.")

    # ── Step 3: Create the episode record with the uploaded audio_url ─────────
    async with httpx.AsyncClient(timeout=30.0) as client:
        logger.info("Step 3/3 — Creating Transistor episode record...")
        create_response = await client.post(
            f"{TRANSISTOR_API_BASE}/episodes",
            headers=_transistor_headers(),
            json={
                "episode": {
                    "show_id": config.HELLOAUDIO_PODCAST_ID,
                    "title": title,
                    "summary": description,
                    "description": description,
                    "audio_url": audio_url,
                }
            },
        )

        if create_response.status_code not in (200, 201):
            raise RuntimeError(
                f"Transistor create episode failed (status {create_response.status_code}): "
                f"{create_response.text[:500]}"
            )

        episode_data = create_response.json()
        episode_id = episode_data["data"]["id"]
        episode_share_url = (
            episode_data.get("data", {})
            .get("attributes", {})
            .get("share_url")
            or f"https://share.transistor.fm/e/{episode_id}"
        )
        logger.info(f"Step 3/3 — Episode created: ID={episode_id}")

        # ── Step 4: Publish the episode ───────────────────────────────────────
        # Transistor creates episodes as drafts. The correct publish endpoint is
        # PATCH /episodes/{id}/publish (NOT PATCH /episodes/{id}).
        logger.info("Step 4/4 — Publishing episode...")
        publish_response = await client.patch(
            f"{TRANSISTOR_API_BASE}/episodes/{episode_id}/publish",
            headers=_transistor_headers(),
            json={"episode": {"status": "published"}},
        )

        if publish_response.status_code not in (200, 201):
            raise RuntimeError(
                f"Transistor publish failed (status {publish_response.status_code}): "
                f"{publish_response.text[:300]}"
            )
        logger.info("Step 4/4 — Episode published successfully.")

    elapsed = time.time() - start_time
    logger.info(
        f"Episode published in {elapsed:.1f}s | "
        f"Title: '{title}' | "
        f"URL: {episode_share_url}"
    )

    return episode_share_url
