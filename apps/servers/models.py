"""Servers are Swarm nodes.

A row is created in one of two ways:

* the control plane's own host is registered by ``sync_cluster`` (``is_local``),
* a remote host is registered by the add-server form and filled in by the
  bootstrap task.

Everything else — role, availability, resources, labels — is mirrored from the
orchestrator, which remains the source of truth.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from apps.core.fields import EncryptedTextField
from apps.core.models import TimeStampedModel

from .constants import STEP_IDS


class SSHKey(TimeStampedModel):
    name = models.CharField(max_length=64, unique=True)
    private_key = EncryptedTextField(
        help_text="PEM or OpenSSH private key. Encrypted at rest with SECRET_KEY."
    )
    public_key = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Server(TimeStampedModel):
    class Role(models.TextChoices):
        MANAGER = "manager", "Manager"
        WORKER = "worker", "Worker"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROVISIONING = "provisioning", "Provisioning"
        READY = "ready", "Ready"
        DRAINING = "draining", "Draining"
        OFFLINE = "offline", "Offline"
        ERROR = "error", "Error"

    class Availability(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSE = "pause", "Paused"
        DRAIN = "drain", "Draining"

    name = models.CharField(max_length=64, unique=True)
    address = models.CharField(
        max_length=255,
        help_text="IP address or hostname the control plane can reach over SSH.",
    )
    ssh_port = models.PositiveIntegerField(default=22)
    ssh_user = models.CharField(max_length=64, default="root")
    ssh_key = models.ForeignKey(
        SSHKey,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="servers",
    )
    ssh_host_key = models.CharField(
        max_length=128,
        blank=True,
        help_text="Pinned SSH host key fingerprint (trust on first use).",
    )

    is_local = models.BooleanField(
        default=False,
        help_text="The host running this control plane. Registered automatically, never SSH'd into.",
    )

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.WORKER)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    availability = models.CharField(
        max_length=16, choices=Availability.choices, default=Availability.ACTIVE
    )
    bootstrap_step = models.CharField(max_length=32, blank=True, choices=[(s, s) for s in STEP_IDS])
    bootstrap_error = models.TextField(blank=True)

    swarm_node_id = models.CharField(max_length=64, blank=True)
    cluster_id = models.CharField(max_length=64, blank=True)
    advertise_addr = models.CharField(
        max_length=255,
        blank=True,
        help_text="Address other nodes should use to reach this one. Defaults to the SSH address.",
    )

    labels = models.JSONField(default=dict, blank=True)

    docker_version = models.CharField(max_length=64, blank=True)
    os_name = models.CharField(max_length=128, blank=True)
    arch = models.CharField(max_length=32, blank=True)
    cpu_cores = models.PositiveIntegerField(null=True, blank=True)
    ram_mb = models.PositiveIntegerField(null=True, blank=True)
    disk_gb = models.PositiveIntegerField(null=True, blank=True)

    is_builder = models.BooleanField(
        default=False, help_text="Runs buildx builds for applications."
    )
    last_seen = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-is_local", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_local"],
                condition=models.Q(is_local=True),
                name="single_local_server",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("servers:detail", args=[self.pk])

    def clean(self) -> None:
        if not self.is_local and not self.swarm_node_id and self.ssh_key_id is None:
            raise ValidationError({"ssh_key": "An SSH key is required for remote servers."})

    # ------------------------------------------------------------------ #
    # Derived state
    # ------------------------------------------------------------------ #

    @property
    def is_remote(self) -> bool:
        return not self.is_local

    @property
    def can_bootstrap(self) -> bool:
        return self.is_remote and self.ssh_key_id is not None

    @property
    def is_manager(self) -> bool:
        return self.role == self.Role.MANAGER

    @property
    def is_ready(self) -> bool:
        return self.status == self.Status.READY

    @property
    def is_busy(self) -> bool:
        return self.status in {self.Status.PENDING, self.Status.PROVISIONING}

    @property
    def step_index(self) -> int:
        try:
            return STEP_IDS.index(self.bootstrap_step) + 1
        except ValueError:
            return 0

    @property
    def step_total(self) -> int:
        return len(STEP_IDS)

    @property
    def status_tone(self) -> str:
        return {
            self.Status.READY: "ok",
            self.Status.PENDING: "muted",
            self.Status.PROVISIONING: "info",
            self.Status.DRAINING: "warn",
            self.Status.OFFLINE: "warn",
            self.Status.ERROR: "bad",
        }.get(self.status, "muted")

    @property
    def ram_display(self) -> str:
        if not self.ram_mb:
            return "—"
        if self.ram_mb >= 1024:
            return f"{self.ram_mb / 1024:.1f} GB"
        return f"{self.ram_mb} MB"
