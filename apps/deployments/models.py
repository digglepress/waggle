from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from apps.core.fields import EncryptedJSONField, EncryptedTextField
from apps.core.models import TimeStampedModel


class Project(TimeStampedModel):
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Environment(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="environments")
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(
            fields=["project", "slug"], name="environment_slug_per_project",
        )]

    def __str__(self):
        return self.name


class SourceConnection(TimeStampedModel):
    class Kind(models.TextChoices):
        GIT = "git", "Git provider"
        REGISTRY = "registry", "Docker registry"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    name = models.CharField(max_length=100)
    host = models.CharField(max_length=260)
    username = models.CharField(max_length=150, blank=True)
    credential = EncryptedTextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.host})"


class Tag(models.Model):
    name = models.SlugField(max_length=36, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TerminalAccess(models.Model):
    """Optional explicit SSH configuration for a Swarm node."""

    server = models.OneToOneField("servers.Server", on_delete=models.CASCADE)
    port = models.PositiveIntegerField(default=2222)
    fingerprint = models.CharField(max_length=64)
    private_key = EncryptedTextField()

    def __str__(self):
        return f"Terminal access for node {self.server_id}"


class Application(TimeStampedModel):
    scope = models.ForeignKey(Environment, on_delete=models.PROTECT, related_name="services")
    connection = models.ForeignKey(SourceConnection, null=True, blank=True, on_delete=models.PROTECT)
    legacy_service_name = models.BooleanField(default=False, editable=False)
    slug = models.SlugField(
        max_length=40,
        validators=[RegexValidator(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")],
    )
    image = models.CharField(max_length=300, blank=True)
    repository = models.URLField(max_length=300, blank=True)
    revision = models.CharField(max_length=40, blank=True)
    build_target = models.CharField(max_length=100, blank=True)
    submodule_connections = models.ManyToManyField(SourceConnection, blank=True, related_name="+")
    port = models.PositiveIntegerField(
        default=8080, validators=[MinValueValidator(1), MaxValueValidator(65535)]
    )
    replicas = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    memory_mb = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(16)]
    )
    cpu_millicores = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)]
    )
    healthcheck = models.CharField(max_length=500, blank=True)
    health_interval = models.PositiveIntegerField(default=15, validators=[MinValueValidator(1)])
    health_timeout = models.PositiveIntegerField(default=5, validators=[MinValueValidator(1)])
    health_start_period = models.PositiveIntegerField(default=10)
    health_retries = models.PositiveIntegerField(default=3, validators=[MinValueValidator(1)])
    environment = EncryptedJSONField(default=dict, blank=True)
    volumes = models.JSONField(default=list, blank=True)
    secret_names = models.JSONField(default=list, blank=True)
    tags = models.ManyToManyField(Tag, blank=True)

    class Meta:
        ordering = ["slug"]
        constraints = [models.UniqueConstraint(
            fields=["scope", "slug"], name="service_slug_per_environment",
        )]

    def __str__(self):
        return self.slug


class ServiceDomain(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="domains")
    hostname = models.CharField(max_length=253, unique=True)

    class Meta:
        ordering = ["hostname"]

    def __str__(self):
        return self.hostname


class Deployment(TimeStampedModel):
    class Operation(models.TextChoices):
        DEPLOY = "deploy", "Deploy"
        ROLLBACK = "rollback", "Rollback"

    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        CONVERGING = "converging", "Waiting for tasks"
        SUBMITTED = "submitted", "Deployed"
        FAILED = "failed", "Failed"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="deployments")
    operation = models.CharField(max_length=12, choices=Operation.choices, default=Operation.DEPLOY)
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    spec = EncryptedJSONField()
    image = models.CharField(max_length=400, blank=True)
    error = models.TextField(blank=True)
    rollback_target = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["application"],
                condition=models.Q(state__in=["queued", "running", "converging"]),
                name="one_active_deployment_per_application",
            )
        ]


#: A deployment in one of these states is still being worked on.
ACTIVE_DEPLOYMENT_STATES = (
    Deployment.State.QUEUED,
    Deployment.State.RUNNING,
    Deployment.State.CONVERGING,
)
