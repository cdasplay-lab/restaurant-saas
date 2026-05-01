# Rollback: Elite Reply Engine

## When to Disable

Disable `ELITE_REPLY_ENGINE` if you observe any of:

- Customer-facing replies are blank or contain only punctuation
- A template appears in place of a valid order reply (e.g., greeting template shown after a customer submits an order)
- Prices, addresses, or order details are missing from a reply that should contain them
- The engine produces wrong-language output (non-Arabic in Arabic-only context)
- Any exception leaking past the `try/except` block in `bot.py`

Do **not** disable for normal banner/tone issues — those are the engine working correctly.

---

## How to Disable Locally

```bash
export ELITE_REPLY_ENGINE=false
uvicorn main:app --reload
```

Or in your `.env` file:
```
ELITE_REPLY_ENGINE=false
```

Then restart the server. The engine is bypassed in `reply_brain.py:275` — `elite_reply_pass()` returns the input reply unchanged.

---

## How to Disable in Production (Render)

1. Open the Render dashboard → your service → **Environment**
2. Find `ELITE_REPLY_ENGINE` (or add it if missing)
3. Set value to `false`
4. Click **Save Changes** — Render will redeploy automatically

The rollback takes effect as soon as the new deploy is live (typically 1–2 minutes).

---

## How to Confirm Old Behavior Is Restored

Send a test message through the bot with a corporate opener in the GPT reply, e.g.:

```
بالتأكيد! أنا هنا لمساعدتك.
```

With `ELITE_REPLY_ENGINE=false`, this phrase will **not** be stripped — the reply passes through unchanged from Algorithm 6.

With `ELITE_REPLY_ENGINE=true`, the engine strips `بالتأكيد` and replaces or cleans the reply.

You can also check the server logs: with the flag off, you will see no `[elite_reply]` log lines.

---

## Re-enabling

```bash
export ELITE_REPLY_ENGINE=true
```

Or remove the env var entirely (default is `true`). Restart the process.
