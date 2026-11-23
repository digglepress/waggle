"""A TextField that is encrypted at rest.

Values are stored as ``enc:v1:<fernet token>``. The prefix makes it obvious in
the database what is encrypted and prevents accidental double encryption.
"""

from __future__ import annotations

import json
from typing import Any

from django.db import models

from .crypto import decrypt, encrypt

PREFIX = "enc:v1:"


class EncryptedTextField(models.TextField):
    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        if value is None or value == "":
            return value
        if not isinstance(value, str) or not value.startswith(PREFIX):
            return value
        return decrypt(value[len(PREFIX) :])

    def get_prep_value(self, value: Any) -> Any:
        if value is None or value == "":
            return value
        text = str(value)
        if text.startswith(PREFIX):
            return text
        return PREFIX + encrypt(text)


class EncryptedJSONField(EncryptedTextField):
    """Opaque JSON: persisted as authenticated ciphertext, not queryable JSON."""

    def from_db_value(self, value, expression, connection):
        raw = super().from_db_value(value, expression, connection)
        return json.loads(raw) if raw is not None else None

    def get_prep_value(self, value):
        if value is None:
            return None
        return super().get_prep_value(json.dumps(value, ensure_ascii=False))
