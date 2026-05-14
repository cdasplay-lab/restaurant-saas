"""
routers/health.py — NUMBER 43: Health endpoints extracted from main.py.

Routes: GET /health, GET /api/health
Unchanged behavior — same response shapes as before.
"""
from fastapi import APIRouter
import os
import database

router = APIRouter()


def _env_present(name: str) -> bool:
    """Return True if the env var is set and non-empty."""
    return bool(os.getenv(name, "").strip())


@router.get("/health")
async def health_check():
    try:
        conn = database.get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "db_backend": "postgresql" if database.IS_POSTGRES else "sqlite",
        "version": "3.0.0",
        "base_url": os.getenv("BASE_URL", "http://localhost:8000").rstrip("/"),
    }


@router.get("/api/health")
async def api_health():
    """Detailed health endpoint: DB + env var presence (no secret values)."""
    try:
        conn = database.get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False

    BASE_URL       = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    META_APP_ID    = os.getenv("META_APP_ID", "")
    META_APP_SECRET = os.getenv("META_APP_SECRET", "")

    jwt_default = (
        os.getenv("JWT_SECRET", "") == "supersecretkey_change_in_production_123456789"
        or not _env_present("JWT_SECRET")
    )
    return {
        "status":        "ok" if db_ok else "degraded",
        "db":            "ok" if db_ok else "error",
        "db_backend":    "postgresql" if database.IS_POSTGRES else "sqlite",
        "version":       "3.0.0",
        "base_url":      "configured" if (BASE_URL and "localhost" not in BASE_URL) else "localhost_or_missing",
        "env": {
            "BASE_URL":                  _env_present("BASE_URL"),
            "DATABASE_URL":              _env_present("DATABASE_URL"),
            "JWT_SECRET":                _env_present("JWT_SECRET") and not jwt_default,
            "OPENAI_API_KEY":            _env_present("OPENAI_API_KEY"),
            "OPENAI_MODEL":              _env_present("OPENAI_MODEL"),
            "META_APP_ID":               _env_present("META_APP_ID"),
            "META_APP_SECRET":           _env_present("META_APP_SECRET"),
            "META_VERIFY_TOKEN":         _env_present("META_VERIFY_TOKEN"),
            "WHATSAPP_VERIFY_TOKEN":     _env_present("WHATSAPP_VERIFY_TOKEN"),
            "WHATSAPP_PHONE_NUMBER_ID":  _env_present("WHATSAPP_PHONE_NUMBER_ID"),
            "ALLOWED_ORIGINS":           _env_present("ALLOWED_ORIGINS"),
        },
        "openai_configured": bool(OPENAI_API_KEY),
        "meta_configured":   bool(META_APP_ID and META_APP_SECRET),
        "base_url_value":    BASE_URL,
    }
