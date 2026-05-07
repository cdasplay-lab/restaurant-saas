"""
Supabase Storage helper — backend only, uses SERVICE_ROLE_KEY.

⚠️  هذا الملف للـ backend فقط — لا تستورده في أي ملف frontend.
    SERVICE_ROLE_KEY لا يُرسل للمتصفح أبداً.

Usage:
    from services import storage as _storage

    url = _storage.upload_bytes(data, _storage.BUCKET_PRODUCTS, "path/file.jpg")
    url = _storage.upload_file("/tmp/file.pdf", _storage.BUCKET_MENUS, "path/file.pdf")
    ok  = _storage.delete_file(_storage.BUCKET_PRODUCTS, "path/file.jpg")

Environment variables (backend only — Railway/Render):
    SUPABASE_URL              — https://xxxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY — service role key   ← backend only, never frontend
    SUPABASE_STORAGE_BUCKET_MENUS    — default: menus
    SUPABASE_STORAGE_BUCKET_PRODUCTS — default: products
"""

from __future__ import annotations

import os
import mimetypes
import logging
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger("storage")

# Bucket names — read at call time so env vars from load_dotenv() are picked up
def _bucket_menus()    -> str: return os.getenv("SUPABASE_STORAGE_BUCKET_MENUS",    "menus")
def _bucket_products() -> str: return os.getenv("SUPABASE_STORAGE_BUCKET_PRODUCTS", "products")

# Public accessors used by main.py
@property
def BUCKET_MENUS(self):    return _bucket_menus()
@property
def BUCKET_PRODUCTS(self): return _bucket_products()

# Module-level aliases (evaluated at call time via functions)
BUCKET_MENUS    = None   # replaced below
BUCKET_PRODUCTS = None   # replaced below


class _BucketProxy:
    """Lazy bucket name — reads env at access time."""
    def __init__(self, key: str, default: str):
        self._key     = key
        self._default = default
    def __str__(self)  -> str: return os.getenv(self._key, self._default)
    def __repr__(self) -> str: return str(self)
    def __eq__(self, other): return str(self) == str(other)
    def __hash__(self):      return hash(str(self))


BUCKET_MENUS          = _BucketProxy("SUPABASE_STORAGE_BUCKET_MENUS",           "menus")
BUCKET_PRODUCTS       = _BucketProxy("SUPABASE_STORAGE_BUCKET_PRODUCTS",        "products")
BUCKET_PAYMENT_PROOFS = _BucketProxy("SUPABASE_STORAGE_BUCKET_PAYMENT_PROOFS",  "payment-proofs")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _is_configured() -> bool:
    """Returns True only if both SUPABASE_URL and SERVICE_ROLE_KEY are set."""
    return bool(os.getenv("SUPABASE_URL", "").strip()
                and os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip())


def _base() -> str:
    return os.getenv("SUPABASE_URL", "").rstrip("/")


def _key() -> str:
    return os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")


def _object_url(bucket: str, path: str) -> str:
    """REST endpoint for upload/delete."""
    return f"{_base()}/storage/v1/object/{str(bucket)}/{path}"


def _public_url(bucket: str, path: str) -> str:
    """Public CDN URL — bucket must be set to public in Supabase Dashboard."""
    return f"{_base()}/storage/v1/object/public/{str(bucket)}/{path}"


def _auth_headers(extra: Optional[dict] = None) -> dict:
    h = {
        "Authorization": f"Bearer {_key()}",
        "apikey":        _key(),
    }
    if extra:
        h.update(extra)
    return h


# ── Upload functions ──────────────────────────────────────────────────────────

def upload_bytes(
    data:         bytes,
    bucket:       str,
    storage_path: str,
    content_type: str = "application/octet-stream",
) -> Optional[str]:
    """
    Upload raw bytes to Supabase Storage.

    Returns:
        Public URL string on success.
        None if Supabase is not configured (local dev — silent fallback).

    Raises:
        RuntimeError if configured but upload fails.
    """
    if not _is_configured():
        logger.debug("Supabase not configured — skipping upload (local dev mode)")
        return None

    import httpx

    url = _object_url(bucket, storage_path)
    headers = _auth_headers({
        "Content-Type": content_type,
        "x-upsert":     "true",
    })

    try:
        resp = httpx.put(url, content=data, headers=headers, timeout=60)
    except httpx.TimeoutException:
        raise RuntimeError(f"Supabase upload timeout: {storage_path}")
    except Exception as e:
        raise RuntimeError(f"Supabase upload network error: {e}") from e

    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Supabase upload failed [{resp.status_code}] "
            f"bucket={bucket} path={storage_path}: {resp.text[:300]}"
        )

    pub = _public_url(bucket, storage_path)
    logger.info(f"Supabase upload OK: {pub}")
    return pub


def upload_file(
    local_path:   str,
    bucket:       str,
    storage_path: str,
    content_type: Optional[str] = None,
) -> Optional[str]:
    """
    Upload a local file to Supabase Storage.

    Returns:
        Public URL on success, None if not configured.
    """
    if not _is_configured():
        return None

    if content_type is None:
        content_type, _ = mimetypes.guess_type(local_path)
        content_type = content_type or "application/octet-stream"

    with open(local_path, "rb") as f:
        data = f.read()

    return upload_bytes(data, bucket, storage_path, content_type)


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_file(bucket: str, storage_path: str) -> bool:
    """
    Delete a file from Supabase Storage.
    Returns True on success, False if not configured or error.
    """
    if not _is_configured():
        return False

    import httpx

    url = _object_url(bucket, storage_path)
    try:
        resp = httpx.delete(url, headers=_auth_headers(), timeout=30)
        success = resp.status_code in (200, 204)
        if not success:
            logger.warning(f"Supabase delete [{resp.status_code}]: {storage_path}")
        return success
    except Exception as e:
        logger.error(f"Supabase delete error: {e}")
        return False


def delete_files(bucket: str, paths: List[str]) -> int:
    """Delete multiple files. Returns count of successful deletions."""
    if not _is_configured() or not paths:
        return 0

    import httpx

    # Supabase supports batch delete via POST /storage/v1/object/bucket
    url = f"{_base()}/storage/v1/object/{str(bucket)}"
    headers = _auth_headers({"Content-Type": "application/json"})

    import json as _json
    try:
        resp = httpx.delete(url, content=_json.dumps({"prefixes": paths}),
                            headers=headers, timeout=30)
        if resp.status_code in (200, 204):
            logger.info(f"Supabase batch delete OK: {len(paths)} files")
            return len(paths)
        logger.warning(f"Supabase batch delete [{resp.status_code}]: {resp.text[:200]}")
        return 0
    except Exception as e:
        logger.error(f"Supabase batch delete error: {e}")
        return 0


# ── Path helpers (consistent naming convention) ───────────────────────────────

def product_image_path(restaurant_id: str, product_id: str, filename: str) -> str:
    """
    Standard storage path for product images.
    Result: restaurants/{restaurant_id}/products/{product_id}/{uuid}.ext
    """
    safe = Path(filename).name
    return f"restaurants/{restaurant_id}/products/{product_id}/{safe}"


def gallery_image_path(restaurant_id: str, product_id: str, filename: str) -> str:
    """
    Standard storage path for product gallery images.
    Result: restaurants/{restaurant_id}/gallery/{product_id}/{uuid}.ext
    """
    safe = Path(filename).name
    return f"restaurants/{restaurant_id}/gallery/{product_id}/{safe}"


def menu_file_path(restaurant_id: str, session_id: str, filename: str) -> str:
    """
    Standard storage path for menu import files.
    Result: restaurants/{restaurant_id}/menus/{session_id}/{filename}
    """
    safe = Path(filename).name
    return f"restaurants/{restaurant_id}/menus/{session_id}/{safe}"


def payment_proof_path(restaurant_id: str, request_id: str, filename: str) -> str:
    """
    Standard storage path for payment proofs.
    Result: payment-proofs/{restaurant_id}/{request_id}/{uuid}.ext
    """
    safe = Path(filename).name
    return f"payment-proofs/{restaurant_id}/{request_id}/{safe}"


def menu_image_path(restaurant_id: str, filename: str) -> str:
    """
    Standard storage path for menu section images.
    Result: restaurants/{restaurant_id}/menu-images/{filename}
    """
    safe = Path(filename).name
    return f"restaurants/{restaurant_id}/menu-images/{safe}"


# ── Backward-compat aliases (used in existing main.py code) ──────────────────
product_storage_path = product_image_path
menu_storage_path    = menu_file_path
