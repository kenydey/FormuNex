"""Access tokens (HS256 JWT) and refresh-token secrets.

Access tokens are short-lived (``auth_access_ttl_minutes``) and stateless; the
refresh token is an opaque random string stored only as a SHA-256 (see
``UserSessionRow``). The JWT is built with the standard library — header and
claims are fixed, so a general JWT library would only add attack surface
(``alg: none``, key confusion); :func:`decode_access_token` accepts exactly
``HS256`` and verifies ``exp``, ``iat``, ``typ`` and ``iss``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from functools import lru_cache
from pathlib import Path

from ...config import get_settings

logger = logging.getLogger(__name__)

ISSUER = "formunex"
_SECRET_PATH = Path("data/.jwt_secret")
_HEADER = {"alg": "HS256", "typ": "JWT"}
ACCESS_PREFIX = "eyJ"  # base64url of '{"' — how the auth middleware tells a JWT from an API key


class InvalidToken(ValueError):
    pass


class SigningKeyMissing(RuntimeError):
    """Production without a configured signing key: sign-in is unavailable (HTTP 503)."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@lru_cache(maxsize=4)
def _secret_for(configured: str, environment: str) -> bytes:
    if environment.strip().lower() in ("production", "prod") and len(configured) < 32:
        raise SigningKeyMissing("FORMUNEX_AUTH_JWT_SECRET (≥ 32 characters) is required in production")
    if configured:
        return configured.encode("utf-8")
    _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(_SECRET_PATH), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, secrets.token_urlsafe(48).encode("ascii"))
        finally:
            os.close(fd)
        logger.warning("auth: generated a development JWT secret at %s — set FORMUNEX_AUTH_JWT_SECRET", _SECRET_PATH)
    except FileExistsError:
        pass
    value = _SECRET_PATH.read_text(encoding="ascii").strip()
    if not value:
        raise RuntimeError(f"{_SECRET_PATH} is empty; delete it or set FORMUNEX_AUTH_JWT_SECRET")
    return value.encode("ascii")


def _secret() -> bytes:
    s = get_settings()
    return _secret_for(s.auth_jwt_secret, s.environment)


def issue_access_token(user_id: str, session_id: str, *, now: float | None = None) -> tuple[str, int]:
    """Return ``(token, expires_in_seconds)``."""
    ttl = max(1, int(get_settings().auth_access_ttl_minutes)) * 60
    iat = int(now if now is not None else time.time())
    claims = {"iss": ISSUER, "sub": user_id, "sid": session_id, "typ": "access", "iat": iat, "exp": iat + ttl}
    signing_input = f"{_b64(json.dumps(_HEADER, separators=(',', ':')).encode())}." \
                    f"{_b64(json.dumps(claims, separators=(',', ':')).encode())}"
    sig = hmac.new(_secret(), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(sig)}", ttl


def decode_access_token(token: str, *, now: float | None = None) -> dict:
    try:
        head_b64, claims_b64, sig_b64 = token.split(".")
        header = json.loads(_unb64(head_b64))
        claims = json.loads(_unb64(claims_b64))
        sig = _unb64(sig_b64)
    except (ValueError, TypeError) as exc:
        raise InvalidToken("malformed token") from exc
    if header != _HEADER:
        raise InvalidToken("unsupported token header")
    expected = hmac.new(_secret(), f"{head_b64}.{claims_b64}".encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise InvalidToken("bad signature")
    if not isinstance(claims, dict) or claims.get("iss") != ISSUER or claims.get("typ") != "access":
        raise InvalidToken("wrong token type")
    t = now if now is not None else time.time()
    exp, iat = claims.get("exp"), claims.get("iat")
    if not isinstance(exp, int) or not isinstance(iat, int) or exp <= t or iat > t + 60:
        raise InvalidToken("token expired")
    if not isinstance(claims.get("sub"), str):
        raise InvalidToken("token has no subject")
    return claims


def looks_like_access_token(token: str) -> bool:
    return token.startswith(ACCESS_PREFIX) and token.count(".") == 2


def new_refresh_token() -> tuple[str, str]:
    """``(token, sha256)`` — hand the token to the client, store only the hash."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
