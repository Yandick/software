from __future__ import annotations

import hashlib
import hmac
import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_LEGACY_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_PASSWORD_HASHER = PasswordHasher(
    hash_len=32,
    memory_cost=65_536,
    parallelism=4,
    salt_len=16,
    time_cost=3,
)


def _legacy_sha256(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def is_legacy_password_hash(password_hash: str) -> bool:
    return bool(_LEGACY_SHA256_RE.fullmatch(password_hash or ""))


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if is_legacy_password_hash(password_hash):
        return hmac.compare_digest(_legacy_sha256(password), password_hash)
    try:
        return bool(_PASSWORD_HASHER.verify(password_hash, password))
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    if is_legacy_password_hash(password_hash):
        return True
    try:
        return _PASSWORD_HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True
