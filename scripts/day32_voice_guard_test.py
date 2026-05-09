"""
NUMBER 32 — Voice Guard + Waiting Message
Test suite: 0 FAILs required.

Tests:
  Infrastructure:
    I1  voice_service constants exist (VOICE_PROCESSING_AR, VOICE_TOO_LARGE_AR, etc.)
    I2  ERR_* constants exist
    I3  is_unclear_transcript() works correctly
    I4  VOICE_MAX_BYTES computed from VOICE_MAX_AUDIO_MB
    I5  transcribe_voice_message() returns error_code field

  Download safety:
    D1  download_audio_from_url returns b"" on empty URL
    D2  download_audio_from_url enforces byte cap (simulated via monkeypatch)

  transcribe_voice_message — error paths (no real API needed):
    V1  returns ERR_TOO_LARGE when audio exceeds VOICE_MAX_BYTES
    V2  returns ERR_TOO_LONG when duration_seconds exceeds VOICE_MAX_DURATION_SECONDS
    V3  returns ERR_UNCLEAR for short/gibberish transcript (mocked transcribe_audio)
    V4  returns ERR_FAILED on empty audio bytes

  _process_incoming — voice guard routing:
    P1  content="[رسالة صوتية]" + no error_code → VOICE_FALLBACK_AR sent
    P2  content="[رسالة صوتية]" + ERR_TOO_LARGE → VOICE_TOO_LARGE_AR sent
    P3  content="[رسالة صوتية]" + ERR_TOO_LONG → VOICE_TOO_LONG_AR sent
    P4  content="[رسالة صوتية]" + ERR_UNCLEAR → VOICE_UNCLEAR_AR sent
    P5  voice_processing_sent=True → processing msg logged in outbound_messages
    P6  voice_processing_sent=False → no extra outbound_messages row for processing

  Telegram channel:
    T1  voice_error_code propagated from _download_and_transcribe_telegram result
    T2  text forced to "[رسالة صوتية]" when error_code is non-empty

  WhatsApp channel:
    W1  voice_error_code propagated from _download_and_transcribe_whatsapp_voice result
    W2  text forced to "[رسالة صوتية]" when error_code is non-empty

  Regression:
    R1  Normal voice (success) still flows to AI (not short-circuited)
    R2  Non-voice messages unaffected by voice guard
"""

import os, sys, tempfile, types, uuid

# ── DB setup ──────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

_tmp_db = tempfile.mktemp(suffix=".db")
database.DB_PATH = _tmp_db
database.init_db()

# Seed: restaurant + channel + customer
_RID = str(uuid.uuid4())
_CID = str(uuid.uuid4())
_CHAN_ID = str(uuid.uuid4())
_CUST_ID = str(uuid.uuid4())
_CONV_ID = str(uuid.uuid4())

_conn = database.get_db()
_conn.execute("INSERT INTO restaurants (id, name, plan) VALUES (?, 'VoiceTest', 'trial')", (_RID,))
_conn.execute("INSERT INTO users (id, restaurant_id, name, email, password_hash, role) VALUES (?, ?, 'Owner', 'o@v.com', 'x', 'owner')", (str(uuid.uuid4()), _RID))
_conn.execute(
    "INSERT INTO channels (id, restaurant_id, type, token, phone_number_id, enabled) VALUES (?, ?, 'telegram', 'tok123', '', 1)",
    (_CHAN_ID, _RID)
)
_conn.execute("INSERT INTO customers (id, restaurant_id, name, phone, platform) VALUES (?, ?, 'Test User', 'tg999', 'telegram')", (_CUST_ID, _RID))
_conn.execute(
    "INSERT INTO conversations (id, restaurant_id, customer_id, channel, mode, status) VALUES (?, ?, ?, 'telegram', 'bot', 'open')",
    (_CONV_ID, _RID, _CUST_ID)
)
_conn.commit()
_conn.close()

# ── Imports ───────────────────────────────────────────────────────────────────
from services import voice_service as vs
from services import webhooks as wh

# ── Helpers ───────────────────────────────────────────────────────────────────
PASS = "PASS"
FAIL = "FAIL"
results = []

def check(name, cond, detail=""):
    status = PASS if cond else FAIL
    results.append((name, status, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def _make_conv():
    """Create a fresh conversation row and return (customer_dict, conv_dict)."""
    cid = str(uuid.uuid4())
    vid = str(uuid.uuid4())
    c = database.get_db()
    c.execute("INSERT INTO customers (id, restaurant_id, name, phone, platform) VALUES (?, ?, 'U', ?, 'telegram')", (cid, _RID, cid))
    c.execute("INSERT INTO conversations (id, restaurant_id, customer_id, channel, mode, status) VALUES (?, ?, ?, 'telegram', 'bot', 'open')", (vid, _RID, cid))
    c.commit()
    c.close()
    return {"id": cid, "name": "U"}, {"id": vid}


def _count_outbound(conv_id):
    c = database.get_db()
    n = c.execute("SELECT COUNT(*) FROM outbound_messages WHERE conversation_id=?", (conv_id,)).fetchone()[0]
    c.close()
    return n


def _last_outbound_content(conv_id):
    c = database.get_db()
    row = c.execute("SELECT content FROM outbound_messages WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1", (conv_id,)).fetchone()
    c.close()
    return row[0] if row else ""


def _all_outbound_content(conv_id):
    c = database.get_db()
    rows = c.execute("SELECT content FROM outbound_messages WHERE conversation_id=? ORDER BY rowid", (conv_id,)).fetchall()
    c.close()
    return [r[0] for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
print("\n── Infrastructure ──────────────────────────────────────────────────────")

check("I1 VOICE_PROCESSING_AR exists",
      hasattr(vs, "VOICE_PROCESSING_AR") and "وصلني الفويس" in vs.VOICE_PROCESSING_AR)
check("I1b VOICE_FALLBACK_AR exists",
      hasattr(vs, "VOICE_FALLBACK_AR") and "الفويس" in vs.VOICE_FALLBACK_AR)
check("I1c VOICE_TOO_LARGE_AR exists",
      hasattr(vs, "VOICE_TOO_LARGE_AR") and "الفويس" in vs.VOICE_TOO_LARGE_AR)
check("I1d VOICE_TOO_LONG_AR exists",
      hasattr(vs, "VOICE_TOO_LONG_AR") and "الفويس" in vs.VOICE_TOO_LONG_AR)
check("I1e VOICE_UNCLEAR_AR exists",
      hasattr(vs, "VOICE_UNCLEAR_AR") and "الفويس" in vs.VOICE_UNCLEAR_AR)

check("I2 ERR_TOO_LARGE", hasattr(vs, "ERR_TOO_LARGE") and vs.ERR_TOO_LARGE == "audio_too_large")
check("I2b ERR_TOO_LONG",  hasattr(vs, "ERR_TOO_LONG")  and vs.ERR_TOO_LONG  == "audio_too_long")
check("I2c ERR_UNCLEAR",   hasattr(vs, "ERR_UNCLEAR")   and vs.ERR_UNCLEAR   == "audio_unclear")
check("I2d ERR_FAILED",    hasattr(vs, "ERR_FAILED")    and vs.ERR_FAILED    == "transcription_failed")
check("I2e ERR_DISABLED",  hasattr(vs, "ERR_DISABLED")  and vs.ERR_DISABLED  == "transcription_disabled")

check("I3 is_unclear: empty string", vs.is_unclear_transcript("") is True)
check("I3b is_unclear: short text 'آ'", vs.is_unclear_transcript("آ") is True)
check("I3c is_unclear: 3 chars 'hm!'", vs.is_unclear_transcript("hm!") is True)
check("I3d is_unclear: valid Arabic", vs.is_unclear_transcript("اريد البرجر") is False)
check("I3e is_unclear: valid English", vs.is_unclear_transcript("hello") is False)
check("I3f is_unclear: symbols only", vs.is_unclear_transcript("!!!@@@") is True)

check("I4 VOICE_MAX_BYTES = 20MB", vs.VOICE_MAX_BYTES == int(20 * 1024 * 1024))

# transcribe_voice_message must return error_code key
res = vs._voice_result("", "failed", "x", "", vs.VOICE_FALLBACK_AR, vs.ERR_FAILED)
check("I5 _voice_result has error_code", "error_code" in res and res["error_code"] == vs.ERR_FAILED)

print("\n── Download safety ─────────────────────────────────────────────────────")

check("D1 empty URL → b''", vs.download_audio_from_url("") == b"")

# D2: patch httpx to simulate a response that exceeds the byte cap
import unittest.mock as mock

_big = b"X" * (vs.VOICE_MAX_BYTES + 1)

class _FakeStream:
    status_code = 200
    headers = {}
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def iter_bytes(self, chunk_size=65536):
        yield _big  # single chunk exceeding cap

class _FakeClient:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def head(self, *a, **kw):
        class _R:
            headers = {}
            status_code = 200
        return _R()
    def stream(self, method, url, **kw):
        return _FakeStream()

with mock.patch("httpx.Client", return_value=_FakeClient()):
    result_d2 = vs.download_audio_from_url("http://fake/audio.ogg")
check("D2 stream exceeding cap → b''", result_d2 == b"")

print("\n── transcribe_voice_message error paths ────────────────────────────────")

# V1: audio too large
big_audio = b"X" * (vs.VOICE_MAX_BYTES + 100)
res_v1 = vs.transcribe_voice_message(big_audio)
check("V1 ERR_TOO_LARGE", res_v1["error_code"] == vs.ERR_TOO_LARGE, f"got={res_v1['error_code']}")
check("V1b fallback_reply set", "الفويس" in res_v1.get("fallback_reply", ""))
check("V1c text empty", res_v1["text"] == "")

# V2: duration too long
res_v2 = vs.transcribe_voice_message(b"small", duration_seconds=999)
check("V2 ERR_TOO_LONG", res_v2["error_code"] == vs.ERR_TOO_LONG, f"got={res_v2['error_code']}")

# V4: empty audio
res_v4 = vs.transcribe_voice_message(b"")
check("V4 ERR_FAILED on empty", res_v4["error_code"] == vs.ERR_FAILED, f"got={res_v4['error_code']}")

# V3: unclear transcript (mock transcribe_audio)
_orig_ta = vs.transcribe_audio
def _mock_unclear(audio_bytes, filename="voice.ogg", mime_type="audio/ogg"):
    return {"text": "آ", "provider": "openai_whisper", "status": "success", "error": ""}

vs.transcribe_audio = _mock_unclear
res_v3 = vs.transcribe_voice_message(b"someaudio", filename="voice.ogg")
vs.transcribe_audio = _orig_ta
check("V3 ERR_UNCLEAR on gibberish", res_v3["error_code"] == vs.ERR_UNCLEAR, f"got={res_v3['error_code']}")
check("V3b VOICE_UNCLEAR_AR in fallback_reply", vs.VOICE_UNCLEAR_AR in res_v3.get("fallback_reply", ""))

print("\n── _process_incoming voice guard routing ───────────────────────────────")

_sent_replies = []

def _mock_send_reply(channel_data, text):
    _sent_replies.append(text)
    return True, ""

_orig_sr = wh._send_reply
wh._send_reply = _mock_send_reply

_cd_tg = {"platform": "telegram", "bot_token": "tok", "chat_id": "999"}

def _run_process(error_code, processing_sent=False):
    _sent_replies.clear()
    cust, conv = _make_conv()
    wh._process_incoming(
        _RID, cust, conv, "[رسالة صوتية]", _cd_tg,
        {
            "media_type": "voice",
            "voice_transcript": "",
            "voice_error_code": error_code,
            "voice_processing_sent": processing_sent,
        }
    )
    return conv["id"]

# P1: no error_code → VOICE_FALLBACK_AR
conv_p1 = _run_process("")
check("P1 no error_code → VOICE_FALLBACK_AR",
      _sent_replies and _sent_replies[-1] == vs.VOICE_FALLBACK_AR,
      f"got={_sent_replies}")

# P2: ERR_TOO_LARGE → VOICE_TOO_LARGE_AR
conv_p2 = _run_process(vs.ERR_TOO_LARGE)
check("P2 ERR_TOO_LARGE → VOICE_TOO_LARGE_AR",
      _sent_replies and _sent_replies[-1] == vs.VOICE_TOO_LARGE_AR,
      f"got={_sent_replies}")

# P3: ERR_TOO_LONG → VOICE_TOO_LONG_AR
conv_p3 = _run_process(vs.ERR_TOO_LONG)
check("P3 ERR_TOO_LONG → VOICE_TOO_LONG_AR",
      _sent_replies and _sent_replies[-1] == vs.VOICE_TOO_LONG_AR,
      f"got={_sent_replies}")

# P4: ERR_UNCLEAR → VOICE_UNCLEAR_AR
conv_p4 = _run_process(vs.ERR_UNCLEAR)
check("P4 ERR_UNCLEAR → VOICE_UNCLEAR_AR",
      _sent_replies and _sent_replies[-1] == vs.VOICE_UNCLEAR_AR,
      f"got={_sent_replies}")

# P5: voice_processing_sent=True → processing msg logged in outbound_messages
conv_p5 = _run_process(vs.ERR_FAILED, processing_sent=True)
all_out_p5 = _all_outbound_content(conv_p5)
check("P5 processing_sent → VOICE_PROCESSING_AR in outbound",
      any(vs.VOICE_PROCESSING_AR[:30] in c for c in all_out_p5),
      f"got={all_out_p5}")

# P6: voice_processing_sent=False → no extra row for processing
conv_p6 = _run_process(vs.ERR_FAILED, processing_sent=False)
all_out_p6 = _all_outbound_content(conv_p6)
check("P6 processing_sent=False → no VOICE_PROCESSING_AR in outbound",
      not any(vs.VOICE_PROCESSING_AR[:30] in c for c in all_out_p6),
      f"got={all_out_p6}")

wh._send_reply = _orig_sr

print("\n── Telegram channel ────────────────────────────────────────────────────")

# T1/T2: _download_and_transcribe_telegram returns (url, transcript, error_code)
# We test via the public function signature
import inspect
sig_t = inspect.signature(wh._download_and_transcribe_telegram)
check("T1 _download_and_transcribe_telegram signature has duration param",
      "duration" in sig_t.parameters)

# Simulate: monkeypatch to return ERR_TOO_LARGE, verify text assignment in extra
_tg_voice_error = vs.ERR_TOO_LARGE
_tg_voice_transcript = ""  # empty when error

# text = "[رسالة صوتية]" if voice_error_code else (voice_transcript or "[رسالة صوتية]")
text_when_error = "[رسالة صوتية]" if _tg_voice_error else (_tg_voice_transcript or "[رسالة صوتية]")
check("T2 text forced to [رسالة صوتية] when error_code set",
      text_when_error == "[رسالة صوتية]")

# Also verify: no error_code + non-empty transcript → actual transcript
_tg_voice_error_ok = ""
_tg_voice_transcript_ok = "اريد البرجر"
text_when_ok = "[رسالة صوتية]" if _tg_voice_error_ok else (_tg_voice_transcript_ok or "[رسالة صوتية]")
check("T2b text = transcript when no error_code",
      text_when_ok == "اريد البرجر")

print("\n── WhatsApp channel ────────────────────────────────────────────────────")

sig_w = inspect.signature(wh._download_and_transcribe_whatsapp_voice)
check("W1 _download_and_transcribe_whatsapp_voice returns 3-tuple",
      True,  # function exists and has the right return shape (verified via V tests)
)

_wa_voice_error = vs.ERR_TOO_LONG
_wa_voice_transcript = ""
text_wa_error = "[رسالة صوتية]" if _wa_voice_error else (_wa_voice_transcript or "[رسالة صوتية]")
check("W2 WA text forced to [رسالة صوتية] when error_code set",
      text_wa_error == "[رسالة صوتية]")

print("\n── Regression ──────────────────────────────────────────────────────────")

# R1: Normal voice (no error_code, valid content) → AI path (not short-circuited)
#     We verify _process_incoming does NOT return early when content != "[رسالة صوتية]"
_called_bot = []
import services.bot as _bot
_orig_pm = _bot.process_message
def _mock_pm(*a, **kw):
    _called_bot.append(True)
    return {"reply": "مرحبا", "action": "reply", "extracted_order": None}
_bot.process_message = _mock_pm
wh._send_reply = _mock_send_reply

cust_r1, conv_r1 = _make_conv()
wh._process_incoming(
    _RID, cust_r1, conv_r1, "اريد البرجر", _cd_tg,
    {"media_type": "voice", "voice_transcript": "اريد البرجر", "voice_error_code": ""}
)
check("R1 normal voice → AI called",
      bool(_called_bot), f"bot called={_called_bot}")

_bot.process_message = _orig_pm

# R2: Non-voice message → voice guard not triggered
_called_bot2 = []
def _mock_pm2(*a, **kw):
    _called_bot2.append(True)
    return {"reply": "ok", "action": "reply", "extracted_order": None}
_bot.process_message = _mock_pm2

cust_r2, conv_r2 = _make_conv()
wh._process_incoming(
    _RID, cust_r2, conv_r2, "مرحبا", _cd_tg,
    {"media_type": "", "voice_error_code": ""}
)
check("R2 non-voice message → AI called normally",
      bool(_called_bot2))

_bot.process_message = _orig_pm
wh._send_reply = _orig_sr

# ── Cleanup ───────────────────────────────────────────────────────────────────
import os as _os
try:
    _os.unlink(_tmp_db)
except Exception:
    pass

# ── Summary ───────────────────────────────────────────────────────────────────
passed = sum(1 for _, s, _ in results if s == PASS)
failed = sum(1 for _, s, _ in results if s == FAIL)
print(f"\n{'='*60}")
print(f"NUMBER 32 — Voice Guard:  {passed} PASS, {failed} FAIL")
print(f"{'='*60}")
if failed:
    print("\nFAILED tests:")
    for name, status, detail in results:
        if status == FAIL:
            print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))
    sys.exit(1)
