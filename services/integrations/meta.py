"""
services/integrations/meta.py
Meta (Facebook / Instagram / WhatsApp) channel adapters.

All three platforms share the same Graph API and app credentials
(META_APP_ID, META_APP_SECRET).  Three separate adapter classes share
a _MetaBase mixin that handles token exchange and Graph API calls.
"""
import os
import json
import logging
from datetime import datetime, timedelta

import httpx

from .base import BaseChannelAdapter

logger = logging.getLogger(__name__)

import urllib.parse as _urlparse

META_APP_ID       = os.getenv("META_APP_ID", "")
META_APP_SECRET   = os.getenv("META_APP_SECRET", "")
META_WA_CONFIG_ID = os.getenv("META_WA_CONFIG_ID", "")
GRAPH_VERSION     = "v20.0"
GRAPH             = f"https://graph.facebook.com/{GRAPH_VERSION}"

# ── SVG icons (inline, no external font dependency) ───────────────────────────
_FB_ICON = (
    "M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 "
    "10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 "
    "4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 "
    "0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 "
    "23.027 24 18.062 24 12.073z"
)
_IG_ICON = (
    "M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 "
    "4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 "
    "4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 "
    "0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 "
    "0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 "
    "1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 "
    "2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 "
    "4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 "
    "0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 "
    "0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 "
    "5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 "
    "6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 "
    "0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 "
    "0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"
)
_WA_ICON = (
    "M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 "
    "1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 "
    "0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 "
    "2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 "
    "1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 "
    "7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 "
    "9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 "
    "9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 "
    "0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 "
    "11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413Z"
)


# ── Shared Meta base ──────────────────────────────────────────────────────────

class _MetaBase(BaseChannelAdapter):
    """Shared Graph API helpers used by Facebook, Instagram, and WhatsApp adapters."""

    auth_type = "oauth2"
    _scopes: str = ""   # overridden per subclass

    # -- OAuth / token helpers ------------------------------------------------

    def build_auth_url(self, state: str, redirect_uri: str) -> str:
        from urllib.parse import urlencode
        params = {
            "client_id":     META_APP_ID,
            "redirect_uri":  redirect_uri,
            "scope":         self._scopes,
            "state":         state,
            "response_type": "code",
        }
        return "https://www.facebook.com/dialog/oauth?" + urlencode(params)

    def _exchange_short_token(self, code: str, redirect_uri: str) -> str:
        """Exchange authorization code → short-lived user token."""
        r = httpx.post(f"{GRAPH}/oauth/access_token", params={
            "client_id":     META_APP_ID,
            "client_secret": META_APP_SECRET,
            "redirect_uri":  redirect_uri,
            "code":          code,
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise ValueError(data["error"].get("message", "Meta token exchange failed"))
        return data["access_token"]

    def _extend_token(self, short_token: str) -> tuple:
        """Exchange short-lived token → long-lived (60-day) token."""
        r = httpx.get(f"{GRAPH}/oauth/access_token", params={
            "grant_type":        "fb_exchange_token",
            "client_id":         META_APP_ID,
            "client_secret":     META_APP_SECRET,
            "fb_exchange_token": short_token,
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise ValueError(data["error"].get("message", "Meta token extension failed"))
        token = data["access_token"]
        expires_in = data.get("expires_in", 5_184_000)  # default 60 days
        expires_at = (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()
        return token, expires_at

    def _get_user_pages(self, long_token: str) -> list:
        """Fetch pages the user admins: [{id, name, access_token, picture_url}]."""
        r = httpx.get(f"{GRAPH}/me/accounts", params={
            "access_token": long_token,
            "fields": "id,name,access_token,picture{url}",
            "limit": 50,
        }, timeout=15)
        r.raise_for_status()
        pages = []
        for p in r.json().get("data", []):
            pages.append({
                "id":          p["id"],
                "name":        p["name"],
                "access_token": p.get("access_token", ""),
                "picture_url": (p.get("picture") or {}).get("data", {}).get("url", ""),
            })
        return pages

    def refresh_token(self, channel: dict) -> dict:
        """Re-extend the existing long-lived token."""
        current = channel.get("token", "")
        if not current:
            raise ValueError("No token to refresh")
        new_token, expires_at = self._extend_token(current)
        return {
            "token":            new_token,
            "token_expires_at": expires_at,
            "reconnect_needed": 0,
        }

    def test_connection(self, channel: dict) -> dict:
        token = channel.get("token", "")
        if not token:
            return {"success": False, "message": "لا يوجد توكن", "detail": {}, "updates": {
                "connection_status": "disconnected"
            }}
        try:
            r = httpx.get(f"{GRAPH}/me", params={
                "access_token": token,
                "fields": "id,name,picture",
            }, timeout=10)
            data = r.json()
            if "error" in data:
                err = data["error"]
                code = str(err.get("code", ""))
                msg  = err.get("message", "خطأ غير معروف")
                return {
                    "success": False,
                    "message": msg,
                    "detail":  data,
                    "updates": {
                        "connection_status": "error",
                        "last_error":        msg,
                        "reconnect_needed":  1 if code in ("190", "102") else 0,
                    },
                }
            name = data.get("name", data.get("id", ""))
            return {
                "success": True,
                "message": f"متصل — {name}",
                "detail":  data,
                "updates": {
                    "connection_status": "connected",
                    "last_error":        "",
                    "reconnect_needed":  0,
                },
            }
        except Exception as exc:
            return {
                "success": False,
                "message": str(exc),
                "detail":  {},
                "updates": {"connection_status": "error", "last_error": str(exc)},
            }


# ── Facebook Messenger adapter ────────────────────────────────────────────────

class FacebookAdapter(_MetaBase):
    platform     = "facebook"
    display_name = "Facebook Messenger"
    description  = "الرد على رسائل Messenger تلقائياً"
    brand_color  = "text-blue-400"
    brand_bg     = "bg-blue-600"
    icon_svg     = _FB_ICON
    _scopes      = ("pages_messaging,pages_read_engagement,"
                    "pages_manage_metadata,pages_show_list")

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        short      = self._exchange_short_token(code, redirect_uri)
        long, exp  = self._extend_token(short)
        pages      = self._get_user_pages(long)
        return {
            "access_token":     long,
            "token_expires_at": exp,
            "pages":            pages,   # UI shows page picker
            "scopes_granted":   self._scopes,
        }

    def subscribe_webhook(self, channel: dict, base_url: str) -> dict:
        page_token    = channel["token"]
        page_id       = channel["page_id"]
        restaurant_id = channel["restaurant_id"]
        verify_token  = channel.get("verify_token", "")
        callback_url  = self.webhook_url(base_url, restaurant_id)
        app_token     = f"{META_APP_ID}|{META_APP_SECRET}"

        # Subscribe page to app
        r1 = httpx.post(f"{GRAPH}/{page_id}/subscribed_apps", params={
            "access_token":       page_token,
            "subscribed_fields":  "messages,messaging_postbacks,messaging_referrals",
        }, timeout=15)
        r1.raise_for_status()

        # Register webhook on app level
        r2 = httpx.post(f"{GRAPH}/{META_APP_ID}/subscriptions", params={
            "object":        "page",
            "callback_url":  callback_url,
            "verify_token":  verify_token,
            "fields":        "messages,messaging_postbacks",
            "access_token":  app_token,
        }, timeout=15)
        r2.raise_for_status()

        return {"success": True, "detail": r2.json()}


# ── Instagram DM adapter ──────────────────────────────────────────────────────

class InstagramAdapter(_MetaBase):
    platform     = "instagram"
    display_name = "Instagram DM"
    description  = "الرد على رسائل Instagram المباشرة"
    brand_color  = "text-pink-400"
    brand_bg     = "bg-gradient-to-br from-purple-600 to-pink-500"
    icon_svg     = _IG_ICON
    _scopes      = ("instagram_basic,instagram_manage_messages,"
                    "pages_show_list,pages_read_engagement")

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        short     = self._exchange_short_token(code, redirect_uri)
        long, exp = self._extend_token(short)
        pages     = self._get_user_pages(long)

        # For each page, find its linked Instagram Business Account
        ig_accounts = []
        for page in pages:
            try:
                r = httpx.get(f"{GRAPH}/{page['id']}", params={
                    "fields":        "instagram_business_account{id,name,profile_picture_url,username}",
                    "access_token":  page["access_token"],
                }, timeout=10)
                ig = r.json().get("instagram_business_account", {})
                if ig:
                    ig_accounts.append({
                        "id":           ig["id"],
                        "name":         ig.get("name") or ig.get("username", ""),
                        "username":     ig.get("username", ""),
                        "picture_url":  ig.get("profile_picture_url", ""),
                        "page_id":      page["id"],
                        "page_name":    page["name"],
                        "page_token":   page["access_token"],
                    })
            except Exception as exc:
                logger.warning(f"[instagram] skip page {page['id']}: {exc}")

        logger.info(
            f"[instagram] exchange_code — fb_pages={len(pages)} ig_accounts={len(ig_accounts)}"
        )
        for acct in ig_accounts:
            logger.info(
                f"[instagram]   acct id={acct['id']} username={acct.get('username','')} "
                f"page_id={acct.get('page_id','')} "
                f"has_page_token={bool(acct.get('page_token',''))} "
                f"token_prefix={acct.get('page_token','')[:12] if acct.get('page_token') else 'EMPTY'}"
            )
        return {
            "access_token":     long,
            "token_expires_at": exp,
            "accounts":         ig_accounts,   # UI shows account picker
            "scopes_granted":   self._scopes,
        }

    def subscribe_webhook(self, channel: dict, base_url: str) -> dict:
        page_token    = channel["token"]
        page_id       = channel["page_id"]
        restaurant_id = channel["restaurant_id"]
        verify_token  = channel.get("verify_token", "")
        callback_url  = self.webhook_url(base_url, restaurant_id)
        app_token     = f"{META_APP_ID}|{META_APP_SECRET}"

        r1 = httpx.post(f"{GRAPH}/{page_id}/subscribed_apps", params={
            "access_token":      page_token,
            "subscribed_fields": "messages,messaging_postbacks,instagram_manage_messages",
        }, timeout=15)
        r1.raise_for_status()

        r2 = httpx.post(f"{GRAPH}/{META_APP_ID}/subscriptions", params={
            "object":       "instagram",
            "callback_url": callback_url,
            "verify_token": verify_token,
            "fields":       "messages,messaging_postbacks",
            "access_token": app_token,
        }, timeout=15)
        r2.raise_for_status()

        return {"success": True, "detail": r2.json()}


# ── WhatsApp Business adapter ─────────────────────────────────────────────────

class WhatsAppAdapter(_MetaBase):
    platform     = "whatsapp"
    display_name = "WhatsApp Business"
    description  = "إرسال واستقبال رسائل WhatsApp Business"
    brand_color  = "text-green-400"
    brand_bg     = "bg-green-600"
    icon_svg     = _WA_ICON
    auth_type    = "oauth2"   # server-side redirect like Facebook/Instagram

    def build_auth_url(self, state: str, redirect_uri: str) -> str:
        """Build WhatsApp Business Login OAuth URL using config_id."""
        if not META_WA_CONFIG_ID:
            raise ValueError("META_WA_CONFIG_ID غير مضبوط — أضفه من Meta Business Manager → Facebook Login for Business → Configuration ID")
        return (
            f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
            f"?client_id={META_APP_ID}"
            f"&redirect_uri={_urlparse.quote(redirect_uri, safe='')}"
            f"&config_id={META_WA_CONFIG_ID}"
            f"&response_type=code"
            f"&state={state}"
        )

    def exchange_code(self, code: str, redirect_uri: str = "") -> dict:
        """Exchange redirect code → long-lived token, then fetch phone numbers for picker."""
        params = {
            "client_id":     META_APP_ID,
            "client_secret": META_APP_SECRET,
            "code":          code,
        }
        if redirect_uri:
            params["redirect_uri"] = redirect_uri

        r = httpx.get(f"{GRAPH}/oauth/access_token", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise ValueError(data["error"].get("message", "WhatsApp token exchange failed"))

        short_token = data["access_token"]

        # Extend to long-lived token (~60 days)
        try:
            token, expires_at = self._extend_token(short_token)
        except Exception as exc:
            logger.warning(f"[wa] token extension failed, using short-lived: {exc}")
            token      = short_token
            expires_in = data.get("expires_in", 0)
            expires_at = (
                (datetime.utcnow() + timedelta(seconds=expires_in)).isoformat()
                if expires_in else ""
            )

        pages = self._get_wa_phone_pages(token)
        logger.info(f"[wa] exchange_code OK — token={'YES' if token else 'NO'} pages={len(pages)}")

        return {
            "access_token":     token,
            "token_expires_at": expires_at,
            "scopes_granted":   "whatsapp_business_management,whatsapp_business_messaging",
            "pages":            pages,
        }

    def _get_wa_phone_pages(self, token: str) -> list:
        """Return WABA phone numbers as page-picker compatible objects."""
        try:
            r = httpx.get(f"{GRAPH}/me/whatsapp_business_accounts", params={
                "access_token": token,
                "fields":       "id,name",
            }, timeout=15)
            r.raise_for_status()
            wabas = r.json().get("data", [])
        except Exception as exc:
            logger.warning(f"[wa] get_waba_accounts failed: {exc}")
            return []

        pages = []
        for waba in wabas:
            waba_id   = waba["id"]
            waba_name = waba.get("name", "WhatsApp Business")
            try:
                ph_r = httpx.get(f"{GRAPH}/{waba_id}/phone_numbers", params={
                    "access_token": token,
                    "fields":       "id,display_phone_number,verified_name",
                }, timeout=15)
                ph_r.raise_for_status()
                for phone in ph_r.json().get("data", []):
                    pid   = phone["id"]
                    pdisp = phone.get("display_phone_number", "")
                    pname = phone.get("verified_name") or pdisp or waba_name
                    pages.append({
                        "id":           waba_id,   # → account_id in select-page = waba_id
                        "name":         pdisp or pname,
                        "access_token": token,
                        "page_id":      pid,       # → page_id in select-page = phone_number_id
                        "page_name":    pdisp or pname,
                        "account_name": waba_name,
                    })
            except Exception as exc:
                logger.warning(f"[wa] get_phone_numbers for waba {waba_id} failed: {exc}")

        return pages

    def get_waba_phone_numbers(self, token: str, waba_id: str) -> list:
        """Return phone numbers registered under a WhatsApp Business Account."""
        r = httpx.get(f"{GRAPH}/{waba_id}/phone_numbers", params={
            "access_token": token,
            "fields": "id,display_phone_number,verified_name,quality_rating",
        }, timeout=15)
        r.raise_for_status()
        return r.json().get("data", [])

    def subscribe_webhook(self, channel: dict, base_url: str) -> dict:
        waba_id       = channel.get("waba_id") or channel.get("business_account_id", "")
        restaurant_id = channel["restaurant_id"]
        verify_token  = channel.get("verify_token", "")
        callback_url  = self.webhook_url(base_url, restaurant_id)
        app_token     = f"{META_APP_ID}|{META_APP_SECRET}"

        r = httpx.post(f"{GRAPH}/{META_APP_ID}/subscriptions", params={
            "object":       "whatsapp_business_account",
            "callback_url": callback_url,
            "verify_token": verify_token,
            "fields":       "messages",
            "access_token": app_token,
        }, timeout=15)
        r.raise_for_status()

        return {"success": True, "detail": r.json()}
