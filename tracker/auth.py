"""Authentication helpers: password hashing and stateless signed tokens.

Tokens are signed with the app ``SECRET_KEY`` via itsdangerous — no server-side
session table is needed, and a token carries only the user id. Password hashes
use werkzeug's PBKDF2 implementation. Both itsdangerous and werkzeug ship with
Flask, so no extra dependency is introduced.
"""

import os
import re
import secrets
from typing import Optional

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import generate_password_hash, check_password_hash

# 30 days — tokens are long-lived so users stay signed in across sessions.
TOKEN_MAX_AGE = 30 * 24 * 3600
_TOKEN_SALT = "tradeski-auth-v1"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Fallback secret for local development only. In production SECRET_KEY MUST be
# set; without it, tokens would not survive a process restart. Generated once
# per process so local dev works without configuration.
_DEV_SECRET = secrets.token_urlsafe(32)


def get_secret_key() -> str:
    return os.environ.get("SECRET_KEY") or _DEV_SECRET


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_secret_key(), salt=_TOKEN_SALT)


# ── Validation ───────────────────────────────────────────────────────────

def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip())) and len(email) <= 254


def password_problem(password: str) -> Optional[str]:
    """Return an error string if the password is unacceptable, else None."""
    if not password or len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password) > 200:
        return "Password must be at most 200 characters."
    return None


# ── Password hashing ─────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, password)


# ── Tokens ───────────────────────────────────────────────────────────────

def generate_token(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def verify_token(token: str, max_age: int = TOKEN_MAX_AGE) -> Optional[int]:
    """Return the user id embedded in a valid, unexpired token, else None."""
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    return uid if isinstance(uid, int) else None
