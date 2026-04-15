"""
services/integrations/base.py
BaseChannelAdapter — contract that every platform adapter must implement.
Adapters are stateless singletons; they receive a channel dict on every call.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta


class BaseChannelAdapter(ABC):
    # ── Class-level metadata (must override in subclass) ──────────────────────
    platform: str       = ""   # "facebook" | "instagram" | "whatsapp" | …
    display_name: str   = ""   # "Facebook Messenger"
    description: str    = ""   # Short description shown on the card
    auth_type: str      = ""   # "oauth2" | "embedded_signup" | "bot_token" | "manual"
    icon_svg: str       = ""   # Inline SVG path data for the icon
    brand_color: str    = ""   # Tailwind text colour class, e.g. "text-blue-500"
    brand_bg: str       = ""   # Tailwind bg colour class, e.g. "bg-blue-600"
    supports_webhook: bool = True

    # ── Connection lifecycle ──────────────────────────────────────────────────

    @abstractmethod
    def build_auth_url(self, state: str, redirect_uri: str) -> str:
        """
        Return the URL to redirect the browser to for OAuth.
        Raise NotImplementedError for 'embedded_signup' or 'bot_token' platforms
        (those use different flows handled entirely in the JS or the backend).
        """

    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        """
        Exchange an OAuth authorization code (or Embedded Signup code) for tokens.

        Must return a dict containing at minimum:
            access_token      : str
            token_expires_at  : str   ISO datetime or "" if non-expiring
            account_id        : str   page_id / phone_number_id / etc.
            account_name      : str   human-readable display name
            account_picture   : str   profile picture URL or ""
            scopes_granted    : str   comma-separated

        For platforms that return multiple accounts (FB pages, IG accounts),
        return "pages" or "accounts" key with a list — the UI will show a picker.
        Raises httpx.HTTPStatusError or ValueError on failure.
        """

    @abstractmethod
    def subscribe_webhook(self, channel: dict, base_url: str) -> dict:
        """
        Register this server's webhook URL with the platform.
        channel: full channels row as a dict (includes restaurant_id, token, etc.)
        Returns {"success": True, "detail": {...}} or raises.
        """

    @abstractmethod
    def test_connection(self, channel: dict) -> dict:
        """
        Make a lightweight API call to verify stored credentials are still valid.
        Returns:
            {
              "success": bool,
              "message": str,
              "detail": dict,
              "updates": dict   # fields to write back to channels row
            }
        """

    def refresh_token(self, channel: dict) -> dict:
        """
        Attempt a silent token refresh (platform-specific).
        Returns updated field dict {token, token_expires_at, reconnect_needed, ...}.
        Default: raise NotImplementedError (most platforms need re-auth).
        """
        raise NotImplementedError(f"{self.platform} does not support silent token refresh")

    # ── Utility helpers ───────────────────────────────────────────────────────

    def webhook_url(self, base_url: str, restaurant_id: str) -> str:
        """Standard webhook URL pattern used across all platforms."""
        return f"{base_url}/webhook/{self.platform}/{restaurant_id}"

    def is_token_expiring_soon(self, channel: dict, days: int = 7) -> bool:
        """Return True if the stored token expires within `days` days."""
        exp = (channel.get("token_expires_at") or "").strip()
        if not exp:
            return False
        try:
            exp_dt = datetime.fromisoformat(exp)
            return exp_dt - datetime.utcnow() < timedelta(days=days)
        except (ValueError, TypeError):
            return False

    def is_token_expired(self, channel: dict) -> bool:
        """Return True if the stored token has already expired."""
        exp = (channel.get("token_expires_at") or "").strip()
        if not exp:
            return False
        try:
            return datetime.fromisoformat(exp) < datetime.utcnow()
        except (ValueError, TypeError):
            return False

    def to_catalog_dict(self) -> dict:
        """Serialisable metadata for the /api/integrations/catalog endpoint."""
        return {
            "platform":     self.platform,
            "display_name": self.display_name,
            "description":  self.description,
            "auth_type":    self.auth_type,
            "icon_svg":     self.icon_svg,
            "brand_color":  self.brand_color,
            "brand_bg":     self.brand_bg,
        }
