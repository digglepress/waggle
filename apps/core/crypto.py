"""Symmetric encryption for values we must store but never show in plain text.

The key is derived from ``SECRET_KEY``. Rotating ``SECRET_KEY`` therefore makes
existing encrypted values unreadable, which is the correct, loud failure mode.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@lru_cache(maxsize=4)
def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet(settings.SECRET_KEY).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet(settings.SECRET_KEY).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            "could not decrypt a stored value: SECRET_KEY has changed"
        ) from exc
