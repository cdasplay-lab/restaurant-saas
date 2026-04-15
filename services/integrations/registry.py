"""
services/integrations/registry.py
Platform registry — maps platform string → adapter instance.
Add new adapters here when onboarding future platforms.
"""
from .meta import FacebookAdapter, InstagramAdapter, WhatsAppAdapter

# ── Adapter registry (stateless singletons) ───────────────────────────────────
_REGISTRY: dict = {}

def _register():
    for adapter_cls in [FacebookAdapter, InstagramAdapter, WhatsAppAdapter]:
        inst = adapter_cls()
        _REGISTRY[inst.platform] = inst

_register()


def get_adapter(platform: str):
    """Return the adapter for a platform, or None if not registered."""
    return _REGISTRY.get(platform)


def get_all_adapters() -> list:
    return list(_REGISTRY.values())


# ── Full platform catalog (includes manual/bot-token platforms) ───────────────
# This drives the UI cards.  Platforms with an adapter get OAuth/Signup buttons;
# others get a manual token form.
PLATFORM_CATALOG = {
    "whatsapp": {
        "display_name": "WhatsApp Business",
        "description":  "إرسال واستقبال رسائل WhatsApp Business",
        "auth_type":    "embedded_signup",
        "brand_color":  "text-green-400",
        "brand_bg":     "bg-green-600",
        "order":        1,
    },
    "instagram": {
        "display_name": "Instagram DM",
        "description":  "الرد على رسائل Instagram المباشرة",
        "auth_type":    "oauth2",
        "brand_color":  "text-pink-400",
        "brand_bg":     "bg-gradient-to-br from-purple-600 to-pink-500",
        "order":        2,
    },
    "facebook": {
        "display_name": "Facebook Messenger",
        "description":  "الرد على رسائل Messenger تلقائياً",
        "auth_type":    "oauth2",
        "brand_color":  "text-blue-400",
        "brand_bg":     "bg-blue-600",
        "order":        3,
    },
    "telegram": {
        "display_name": "Telegram",
        "description":  "ربط بوت تيليكرام مع المطعم",
        "auth_type":    "bot_token",
        "brand_color":  "text-sky-400",
        "brand_bg":     "bg-sky-500",
        "order":        4,
    },
    # ── Future platforms (uncomment + add adapter to activate) ────────────────
    # "webchat": {
    #     "display_name": "Web Chat",
    #     "description":  "ويدجت دردشة مباشر على موقعك",
    #     "auth_type":    "snippet",
    #     "brand_color":  "text-indigo-400",
    #     "brand_bg":     "bg-indigo-600",
    #     "order":        5,
    # },
    # "tiktok": {
    #     "display_name": "TikTok",
    #     "description":  "ربط TikTok Messages",
    #     "auth_type":    "oauth2",
    #     "brand_color":  "text-rose-400",
    #     "brand_bg":     "bg-rose-600",
    #     "order":        6,
    # },
}
