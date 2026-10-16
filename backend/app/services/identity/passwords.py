"""Password hashing (argon2id) and the login lock-out policy.

The same verification backs electronic signatures: signing a release step
re-asks for the password (plan §4.4), so a hijacked session alone cannot sign.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters

MIN_LENGTH = 10
MAX_FAILED_LOGINS = 5
LOCKOUT = timedelta(minutes=15)


class WeakPassword(ValueError):
    pass


def check_strength(password: str) -> None:
    """Minimum policy: length, and at least three of four character classes."""
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"密码至少 {MIN_LENGTH} 位")
    classes = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if classes < 3:
        raise WeakPassword("密码需包含大写字母、小写字母、数字、符号中的至少三类")


def hash_password(password: str) -> str:
    check_strength(password)
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    if not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def rehash(password: str) -> str:
    """Re-hash an already-accepted password with current parameters (no strength check: the
    user proved it is theirs; forcing a change at sign-in is a separate policy)."""
    return _hasher.hash(password)


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def is_locked(locked_until: datetime | None, now: datetime) -> bool:
    return locked_until is not None and locked_until > now


def register_failure(failed_logins: int, now: datetime) -> tuple[int, datetime | None]:
    """New ``(failed_logins, locked_until)`` after a failed attempt."""
    failed = failed_logins + 1
    if failed >= MAX_FAILED_LOGINS:
        return 0, now + LOCKOUT
    return failed, None
