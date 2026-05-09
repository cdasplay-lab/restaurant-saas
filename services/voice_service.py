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

# Customer-facing messages — never mention AI, Whisper, or transcription.
VOICE_PROCESSING_AR = "وصلني الفويس 🌷 لحظة أسمعه وأرجعلك"
VOICE_FALLBACK_AR   = "وصلني الفويس 🌷 بس ما كدرت أسمعه بوضوح، تكدر تكتبلي الطلب؟"
VOICE_TOO_LARGE_AR  = "الفويس طويل شوي 🌷 دزلي الطلب مختصر أو اكتبه حتى أخدمك أسرع"
VOICE_TOO_LONG_AR   = "الفويس طويل شوي 🌷 دزلي الطلب مختصر أو اكتبه حتى أخدمك أسرع"
VOICE_UNCLEAR_AR    = "الفويس مو واضح كفاية 🌷 تكدر تكتبه حتى أكمل طلبك؟"

# Error codes (internal, never shown to customer)
ERR_TOO_LARGE  = "audio_too_large"
ERR_TOO_LONG   = "audio_too_long"
ERR_UNCLEAR    = "audio_unclear"
ERR_FAILED     = "transcription_failed"
ERR_DISABLED   = "transcription_disabled"

# ── Unclear transcript detection ─────────────────────────────────────────────

_MIN_MEANINGFUL_CHARS = 4     # shorter than this = unclear
_GIBBERISH_SYMBOLS = set("!@#$%^&*()_+=[]{}|;':\",./<>?\\`~")

def is_unclear_transcript(text: str) -> bool:
    """Return True if the transcript is too short, empty, or gibberish to be useful."""
    if not text or not text.strip():
        return True
    stripped = text.strip()
    if len(stripped) < _MIN_MEANINGFUL_CHARS:
        return True
    # Mostly symbols / non-Arabic non-Latin chars
    alpha_count = sum(1 for c in stripped if c.isalpha())
    if alpha_count < 2:
        return True
    return False

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
    max_bytes: int = 0,
) -> bytes:
    """
    Download audio from a URL safely. Returns empty bytes on any failure.
    Enforces max_bytes cap (defaults to VOICE_MAX_BYTES).
    Uses Content-Length pre-check + streaming byte cap.
    """
    if not url:
        return b""
    cap = max_bytes or VOICE_MAX_BYTES
    try:
        import httpx
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            # Head-only to check Content-Length first (best effort)
            try:
                head = client.head(url, headers=headers or {})
                cl = int(head.headers.get("content-length", 0))
                if cl > cap:
                    logger.warning(f"[voice] Content-Length {cl} > cap {cap} — aborting download")
                    return b""
            except Exception:
                pass  # HEAD not supported — fall through to streaming

            # Stream download with byte cap
            chunks = []
            downloaded = 0
            with client.stream("GET", url, headers=headers or {}) as resp:
                if resp.status_code != 200:
                    logger.warning(f"[voice] download failed — status={resp.status_code} url={url[:80]}")
                    return b""
                # Content-Length from the actual GET response
                cl_get = int(resp.headers.get("content-length", 0))
                if cl_get > cap:
                    logger.warning(f"[voice] Content-Length (GET) {cl_get} > cap — aborting")
                    return b""
                for chunk in resp.iter_bytes(chunk_size=65536):
                    downloaded += len(chunk)
                    if downloaded > cap:
                        logger.warning(f"[voice] stream exceeded cap {cap} bytes — aborting")
                        return b""
                    chunks.append(chunk)

        data = b"".join(chunks)
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
    duration_seconds: int = 0,
) -> dict:
    """
    High-level helper: download + transcribe + return full result dict.

    Returns:
        {
            "text": str,
            "voice_transcript": str,
            "transcription_status": str,   # success | skipped | failed
            "transcription_error": str,    # error code, e.g. audio_too_large
            "transcription_provider": str,
            "transcribed_at": str,
            "fallback_reply": str,         # Arabic reply if status != "success"
            "error_code": str,             # ERR_* constant for structured handling
        }
    """
    import datetime

    if not audio_bytes:
        return _voice_result("", "failed", "no audio data", "", VOICE_FALLBACK_AR, ERR_FAILED)

    # Duration guard (if caller knows the duration from metadata)
    if duration_seconds and duration_seconds > VOICE_MAX_DURATION_SECONDS:
        logger.warning(
            f"[voice] duration {duration_seconds}s > limit {VOICE_MAX_DURATION_SECONDS}s"
        )
        return _voice_result("", "skipped", ERR_TOO_LONG, "", VOICE_TOO_LONG_AR, ERR_TOO_LONG)

    # Size guard
    if len(audio_bytes) > VOICE_MAX_BYTES:
        size_mb = len(audio_bytes) / (1024 * 1024)
        logger.warning(f"[voice] audio too large: {size_mb:.1f} MB > {VOICE_MAX_AUDIO_MB} MB limit")
        return _voice_result("", "skipped", ERR_TOO_LARGE, "", VOICE_TOO_LARGE_AR, ERR_TOO_LARGE)

    result = transcribe_audio(audio_bytes, filename=filename, mime_type=mime_type)
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    # Unclear transcript guard
    if result["status"] == "success" and is_unclear_transcript(result["text"]):
        return _voice_result(
            result["text"], "success", ERR_UNCLEAR,
            result["provider"], VOICE_UNCLEAR_AR, ERR_UNCLEAR,
            transcribed_at=now_str,
        )

    fallback = VOICE_FALLBACK_AR if result["status"] != "success" else ""
    err_code = ERR_FAILED if result["status"] != "success" else ""
    return {
        "text": result["text"],
        "voice_transcript": result["text"],
        "transcription_status": result["status"],
        "transcription_error": result["error"],
        "transcription_provider": result["provider"],
        "transcribed_at": now_str if result["status"] == "success" else "",
        "fallback_reply": fallback,
        "error_code": err_code,
    }


def _voice_result(text, status, error, provider, fallback, error_code="", transcribed_at=""):
    return {
        "text": text,
        "voice_transcript": text,
        "transcription_status": status,
        "transcription_error": error,
        "transcription_provider": provider,
        "transcribed_at": transcribed_at,
        "fallback_reply": fallback,
        "error_code": error_code,
    }
