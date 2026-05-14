"""
dependencies.py — NUMBER 43: Shared FastAPI auth dependencies.

Extracted from main.py to allow routers to import without circular deps.
Functions: verify_token, current_user, require_role, current_super_admin
"""
from __future__ import annotations
import os
import logging
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
import database

logger = logging.getLogger("restaurant-saas")

# ── JWT config ────────────────────────────────────────────────────────────────
# Mirrors main.py — reads same env vars.
# Validation (RuntimeError on unsafe default in prod) stays in main.py startup.
SECRET_KEY = os.getenv(
    "JWT_SECRET",
    os.getenv("SECRET_KEY", "dev_only_insecure_jwt_secret_do_not_use_in_production"),
)
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("SESSION_HOURS", "24"))

bearer = HTTPBearer()


def verify_token(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        if not payload.get("sub"):
            raise HTTPException(status_code=401, detail="Invalid token")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def current_user(payload: dict = Depends(verify_token)):
    conn = database.get_db()
    row = conn.execute("""
        SELECT u.*, r.name AS restaurant_name, r.plan
        FROM users u JOIN restaurants r ON u.restaurant_id = r.id
        WHERE u.id = ?
    """, (payload["sub"],)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="User not found")
    return dict(row)


def require_role(*roles):
    def checker(user=Depends(current_user)):
        if user["role"] not in roles:
            raise HTTPException(403, "غير مصرح — الدور غير كافٍ")
        return user
    return checker


def current_super_admin(payload: dict = Depends(verify_token)):
    """Dependency that ensures the caller is an authenticated super admin."""
    if not payload.get("is_super"):
        raise HTTPException(403, "غير مصرح — يلزم صلاحية super_admin")
    conn = database.get_db()
    row = conn.execute("SELECT * FROM super_admins WHERE id=?", (payload["sub"],)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(401, "حساب super_admin غير موجود")
    return dict(row)
