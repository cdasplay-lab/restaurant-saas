# Legacy Node.js (Express) Backend — ARCHIVED

These files are from an earlier Express.js-based backend that is **no longer used**.

The active backend is **FastAPI (Python)** — see `main.py`.

## What happened

The project originally had a Node.js/Express backend (`server.js` + routes + middleware).
It was replaced by the current FastAPI backend but the old files remained in the repo.

## Files archived

| File | Description |
|------|-------------|
| `db.js` | Empty SQLite database connector (was never functional) |
| `middleware/auth.js` | Express JWT auth middleware |
| `routes/auth.js` | Express auth routes (login, logout, me) |
| `routes/orders.js` | Express orders routes |
| `routes/products.js` | Express products routes |
| `routes/customers.js` | Express customers routes |
| `routes/conversations.js` | Express conversations routes |
| `routes/analytics.js` | Express analytics routes |

## When was this archived

Archived during NUMBER 40 — Verified Production Cleanup.

## Can I delete these?

Yes. These files are not imported or used by the FastAPI application.
They are kept here for reference only and can be safely deleted at any time.
