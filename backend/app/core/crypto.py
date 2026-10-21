"""Symmetric encryption helpers for camera credentials at rest."""

from __future__ import annotations

import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings

_PREFIX = "enc:v1:"


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"scai-camera-credentials-v1",
        info=b"camera-url-encryption",
    )
    key = base64.urlsafe_b64encode(hkdf.derive(settings.secret_key.encode("utf-8")))
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a sensitive string (e.g. a full RTSP URL with password)."""
    if not plaintext:
        return ""
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        # Value stored before the prefix existed / plaintext migration.
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX) :].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError("Unable to decrypt stored credential (secret key changed?)") from exc
