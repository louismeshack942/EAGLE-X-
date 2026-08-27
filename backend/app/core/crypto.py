"""Secret-at-rest helpers.

Used to encrypt Deriv OAuth tokens/secret-material stored in the DB. The encryption key
comes from the SECRET_KEY env var (set in production). A unique, random IV is stored with
each ciphertext. We never rely on a hard-coded/demo key in production.
"""

from __future__ import annotations

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import settings


def _derive_key(secret: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000)
    return kdf.derive(secret.encode("utf-8"))


def _fernet() -> Fernet:
    secret = settings.secret_key
    salt = b"eaglex-encryption-v1"
    key = _derive_key(secret, salt)
    return Fernet(_urlsafe_b64(key))


def _urlsafe_b64(raw: bytes) -> bytes:
    import base64

    return base64.urlsafe_b64encode(raw)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def hash_secret(value: str) -> str:
    """Non-reversible hash for session tokens / verifiers."""
    import hashlib

    return hashlib.sha256(f"{settings.secret_key}:{value}".encode("utf-8")).hexdigest()


__all__ = ["encrypt", "decrypt", "hash_secret"]