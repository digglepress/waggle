from __future__ import annotations

import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditEvent(models.Model):
    """Append-only record of security-relevant operator actions.

    Detail dictionaries must never contain credentials or environment values.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    action = models.CharField(max_length=80)
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    target_label = models.CharField(max_length=200, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.target_label}".strip()


class APIToken(models.Model):
    """A scoped bearer token for automation.

    Only a SHA-256 digest is stored. The raw token is shown once when it is
    issued and cannot be recovered later.
    """

    class Scope(models.TextChoices):
        READ = "read", "Read services and deployments"
        DEPLOY = "deploy", "Queue deployments and rollbacks"

    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=16, db_index=True)
    digest = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix})"

    @classmethod
    def issue(cls, name: str, scopes: list[str]) -> tuple[APIToken, str]:
        raw = f"wgl_{secrets.token_hex(4)}_{secrets.token_urlsafe(32)}"
        token = cls.objects.create(
            name=name,
            prefix=raw.split("_", 2)[1],
            digest=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            scopes=sorted(set(scopes)),
        )
        return token, raw

    @classmethod
    def authenticate(cls, raw: str) -> APIToken | None:
        parts = raw.split("_", 2) if raw else []
        if len(parts) != 3 or parts[0] != "wgl" or not parts[1] or not parts[2]:
            return None
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        token = cls.objects.filter(prefix=parts[1], digest=digest, active=True).first()
        if token is not None:
            now = timezone.now()
            if token.last_used_at is None or (now - token.last_used_at).total_seconds() > 60:
                cls.objects.filter(pk=token.pk).update(last_used_at=now)
        return token

    def allows(self, scope: str) -> bool:
        return scope in (self.scopes or [])

