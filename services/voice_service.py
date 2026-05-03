"""
services/voice_service.py
NUMBER 22 — Voice Advanced

Core voice transcription service with provider abstraction.
Used by webhooks.py for all channel voice handling.

Safe rules:
- Never expose AI/Whisper/OpenAI names to customers.
- Never crash on bad audio.
- Never log API keys.
- Return safe Arabic fallback on any failure.
"""
import os
import io
import logging
from typing import Optional

logger = logging.getLogger("restaurant-saas")

# ── Config ─────────────────────────────────────────────────────────────────

VOICE_TRANSCRIPTION_ENABLED  = os.getenv("VOICE_TRANSCRIPTION_ENABLED", "true").strip().lower() == "true"
VOICE_MAX_AUDIO_MB           = float(os.getenv("VOICE_MAX_AUDIO_MB", "20"))
VOICE_MAX_DURATION_SECONDS   = int(os.getenv("VOICE_MAX_DURATION_SECONDS", "120"))
VOICE_TRANSCRIPTION_PROVIDER = os.getenv("VOICE_TRANSCRIPTION_PROVIDER", "openai")
_OPENAI_API_KEY              = os.getenv("OPENAI_API_KEY", "")

VOICE_MAX_BYTES = int(VOICE_MAX_AUDIO_MB * 1024 * 1024)

# Customer-facing safe fallback — never mention AI, Whisper, or transcription.
VOICE_FALLBACK_AR  = "وصلني الفويس 🌷 بس ما كدرت أسمعه بوضوح، تكدر تكتبلي الطلب؟"
VOICE_TOO_LARGE_AR = "وصلني الفويس 🌷 بس حجمه كبير، ممكن تكتبلي الطلب؟"

# ── Public API ──────────────────────────────────────────────────────────────

def is_voice_enabled() -> bool:
    """Return True if voice transcription is enabled and provider is configured."""
    return VOICE_TRANSCRIPTION_ENABLED and bool(_OPENAI_API_KEY)


def transcribe_audio(
    audio_bytes: bytes,
    filename: str = "voice.ogg",
    mime_type: str = "audio/ogg",
) -> dict:
    """
    Transcribe audio bytes using the configured provider.

    Returns:
        {
            "text": str,           # transcribed text (empty string on failure)
            "provider": str,       # "openai_whisper"
            "status": str,         # "success" | "failed" | "skipped"
            "error": str,          # internal error (never sent to customer)
        }
    """
    if not VOICE_TRANSCRIPTION_ENABLED:
        return {"text": "", "provider": "", "status": "skipped", "error": "transcription disabled"}

    if not _OPENAI_API_KEY:
        return {"text": "", "provider": "", "status": "skipped", "error": "OPENAI_API_KEY not set"}

    if not audio_bytes:
        return {"text": "", "provider": "", "status": "failed", "error": "empty audio bytes"}

    if len(audio_bytes) > VOICE_MAX_BYTES:
        size_mb = len(audio_bytes) / (1024 * 1024)
        logger.warning(f"[voice] audio too large: {size_mb:.1f} MB > {VOICE_MAX_AUDIO_MB} MB limit")
        return {
            "text": "",
            "provider": "openai_whisper",
            "status": "skipped",
            "error": f"audio too large: {size_mb:.1f} MB",
        }

    try:
        import openai as _openai
        client = _openai.OpenAI(api_key=_OPENAI_API_KEY)
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="ar",
        )
        text = (response.text or "").strip()
        logger.info(f"[voice] transcription success — len={len(text)} preview={text[:60]!r}")
        return {
            "text": text,
            "provider": "openai_whisper",
            "status": "success" if text else "failed",
            "error": "" if text else "empty transcript returned",
        }
    except Exception as e:
        logger.error(f"[voice] transcription failed: {e}")
        return {
            "text": "",
            "provider": "openai_whisper",
            "status": "failed",
            "error": str(e)[:200],
        }


def download_audio_from_url(
    url: str,
    headers: Optional[dict] = None,
    timeout: int = 20,
) -> bytes:
    """
    Download audio from a URL safely. Returns empty bytes on any failure.
    Enforces VOICE_MAX_BYTES size limit.
    """
    if not url:
        return b""
    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url, headers=headers or {}, follow_redirects=True)
        if r.status_code != 200:
            logger.warning(f"[voice] download failed — status={r.status_code} url={url[:80]}")
            return b""
        data = r.content
        if len(data) > VOICE_MAX_BYTES:
            logger.warning(f"[voice] downloaded audio exceeds limit: {len(data)} bytes")
            return b""
        logger.info(f"[voice] downloaded {len(data)} bytes from {url[:80]}")
        return data
    except Exception as e:
        logger.error(f"[voice] download error: {e}")
        return b""


def transcribe_voice_message(
    audio_bytes: bytes,
    filename: str = "voice.ogg",
    mime_type: str = "audio/ogg",
    channel: str = "unknown",
    restaurant_id: str = "",
) -> dict:
    """
    High-level helper: download + transcribe + return full result dict.

    Returns:
        {
            "text": str,                # customer text to use as input
            "voice_transcript": str,    # same as text (for DB storage compat)
            "transcription_status": str,
            "transcription_error": str,
            "transcription_provider": str,
            "transcribed_at": str,
            "fallback_reply": str,      # Arabic message to send if status != "success"
        }
    """
    import datetime
    fallback = VOICE_FALLBACK_AR

    if not audio_bytes:
        return _voice_result("", "failed", "no audio data", "", fallback)

    if len(audio_bytes) > VOICE_MAX_BYTES:
        return _voice_result("", "skipped", "audio too large", "", VOICE_TOO_LARGE_AR)

    result = transcribe_audio(audio_bytes, filename=filename, mime_type=mime_type)
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "text": result["text"],
        "voice_transcript": result["text"],
        "transcription_status": result["status"],
        "transcription_error": result["error"],
        "transcription_provider": result["provider"],
        "transcribed_at": now_str if result["status"] == "success" else "",
        "fallback_reply": fallback if result["status"] != "success" else "",
    }


def _voice_result(text, status, error, provider, fallback):
    return {
        "text": text,
        "voice_transcript": text,
        "transcription_status": status,
        "transcription_error": error,
        "transcription_provider": provider,
        "transcribed_at": "",
        "fallback_reply": fallback,
    }
