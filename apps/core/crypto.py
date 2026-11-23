"""Symmetric encryption for values we must store but never show in plain text.

The key ring is derived from ``SECRET_KEYS`` (or ``SECRET_KEY``). New values are
encrypted with the first key; every configured key can decrypt. To rotate:

1. prepend a new random key to ``WAGGLE_SECRET_KEYS``;
2. keep the old key after it until every stored value has been rewritten;
3. remove the old key.

Rotating without keeping the old key makes existing values unreadable. That is
intentional and loud, not a silent fallback.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet(secrets: tuple[str, ...]) -> MultiFernet:
    return MultiFernet([
        Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest()))
        for secret in secrets
    ])


@lru_cache(maxsize=4)
def _cached_fernet(secrets: tuple[str, ...]) -> MultiFernet:
    return _fernet(secrets)


def encrypt(plaintext: str) -> str:
    keys = tuple(getattr(settings, "SECRET_KEYS", ()) or (settings.SECRET_KEY,))
    return _cached_fernet(keys).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    keys = tuple(getattr(settings, "SECRET_KEYS", ()) or (settings.SECRET_KEY,))
    try:
        return _cached_fernet(keys).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ImproperlyConfigured(
            "could not decrypt a stored value: no configured WAGGLE_SECRET_KEYS entry matches"
        ) from exc
