from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import get_settings

_hasher = PasswordHasher()


def hash_secret(value: str) -> str:
    """Argon2id hash for PINs and passwords. Never log the input or the digest."""
    return _hasher.hash(value)


def verify_secret(hashed: str | None, value: str) -> bool:
    if not hashed:
        return False
    try:
        return _hasher.verify(hashed, value)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def hash_token(token: str) -> str:
    """Deterministic digest so opaque tokens are never stored in plaintext."""
    secret = get_settings().secret_key.encode()
    return hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(48)


def generate_otp(digits: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(digits))


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; re-attach UTC so comparisons stay correct across dialects."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_access_token(claims: dict[str, Any], *, expires_in_minutes: int | None = None) -> str:
    """Compact HMAC-SHA256 signed token. Self-contained so reads need no session lookup."""
    settings = get_settings()
    minutes = settings.access_token_minutes if expires_in_minutes is None else expires_in_minutes
    payload = {**claims, "exp": int((utc_now() + timedelta(minutes=minutes)).timestamp())}
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def verify_access_token(token: str) -> dict[str, Any] | None:
    """Return the claims, or ``None`` when the token is malformed, forged, or expired."""
    body, _, provided = token.partition(".")
    if not body or not provided:
        return None
    expected = hmac.new(
        get_settings().secret_key.encode(), body.encode(), hashlib.sha256
    ).digest()
    try:
        if not hmac.compare_digest(_b64decode(provided), expected):
            return None
        claims = json.loads(_b64decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    expires_at = claims.get("exp")
    if not isinstance(expires_at, int) or expires_at <= int(utc_now().timestamp()):
        return None
    return claims
