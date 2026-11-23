import re
from pathlib import PurePosixPath

from django import forms
from django.utils.text import slugify

from apps.core.fields import PREFIX
from apps.deployments.models import Application, Environment, Project, SourceConnection, Tag
from apps.deployments.sources import image_host, registry_host, repository_host, source_host

SECRET = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
TAG = re.compile(r"[a-z0-9][a-z0-9-]{0,35}")
HEALTH_DEFAULTS = {
    "health_interval": 15, "health_timeout": 5, "health_start_period": 10, "health_retries": 3,
}


class StyledForm:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"


class NameForm(StyledForm, forms.Form):
    name = forms.CharField(max_length=80)

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project

    def clean_name(self):
        name = self.cleaned_data["name"]
        slug = slugify(name)
        if not slug:
            raise forms.ValidationError("Use at least one letter or number that can form a URL.")
        choices = self.project.environments if self.project else Project.objects
        if choices.filter(slug=slug).exists():
            raise forms.ValidationError("A name with this URL slug already exists.")
        self.slug = slug
        return name

    def save(self):
        values = {"name": self.cleaned_data["name"], "slug": self.slug}
        if self.project:
            return Environment.objects.create(project=self.project, **values)
        return Project.objects.create(**values)


class ConnectionForm(StyledForm, forms.ModelForm):
    credential = forms.CharField(
        required=False, strip=False, max_length=4096,
        widget=forms.PasswordInput(render_value=False),
    )

    class Meta:
        model = SourceConnection
        fields = ("name", "host", "username", "credential")

    def __init__(self, *args, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.kind = kind
        self.fields["host"].help_text = (
            "e.g. github.com or gitlab.com" if kind == SourceConnection.Kind.GIT
            else "e.g. docker.io, ghcr.io or registry.example.com:5000"
        )
        if kind == SourceConnection.Kind.GIT:
            self.fields.pop("username")
            self.fields["credential"].label = "Git access token"
            self.fields["credential"].help_text = "Leave blank for public repositories."
        else:
            self.fields["credential"].label = "Registry password or access token"
            self.fields["credential"].help_text = "Leave both credentials blank for a public registry."
        if self.instance.pk:
            self.fields["host"].disabled = True
            self.fields["credential"].help_text = "Enter a new credential to rotate it, or leave blank to keep it."

    def clean_host(self):
        normalize = source_host if self.instance.kind == SourceConnection.Kind.GIT else registry_host
        try:
            return normalize(self.cleaned_data["host"])
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_credential(self):
        credential = self.cleaned_data["credential"]
        if not credential and self.instance.pk:
            return self.instance.credential
        if credential.startswith(PREFIX) or any(char in credential for char in ("\0", "\r", "\n")):
            raise forms.ValidationError("Enter a single-line password or access token.")
        return credential

    def clean(self):
        values = super().clean()
        if self.instance.kind == SourceConnection.Kind.REGISTRY:
            if bool(values.get("username")) != bool(values.get("credential")):
                self.add_error("credential", "Provide both username and credential, or leave both blank.")
        return values


class ApplicationForm(StyledForm, forms.ModelForm):
    tags = forms.CharField(required=False, max_length=800, help_text="Comma-separated tags.")

    class Meta:
        model = Application
        fields = (
            "slug", "connection", "image", "repository", "revision", "port", "replicas",
            "build_target", "submodule_connections", "memory_mb", "cpu_millicores", "healthcheck",
            "health_interval", "health_timeout", "health_start_period", "health_retries",
            "volumes", "secret_names",
        )
        labels = {"slug": "Service name", "connection": "Source connection", "image": "Image reference"}
        help_texts = {
            "image": "Image reference such as nginx:1.27 or ghcr.io/org/api:latest.",
            "repository": "HTTPS Git URL ending in .git with a Dockerfile at the repository root.",
            "revision": "Full 40-character Git commit.",
            "build_target": "Optional Dockerfile stage name (buildx --target).",
            "submodule_connections": "Optional credentials for private HTTPS Git submodules on other hosts.",
            "memory_mb": "Optional per-replica memory limit in MiB.",
            "cpu_millicores": "Optional per-replica CPU limit. 1000 is one core.",
            "healthcheck": "Optional shell command run inside the container; exit 0 means healthy.",
            "secret_names": 'JSON list of existing Swarm secrets, e.g. ["db-password"].',
            "volumes": 'JSON list of named mounts, e.g. [{"name":"data","target":"/data","read_only":false}].',
        }

    def __init__(self, *args, scope, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope
        self.kind = kind
        self.instance.scope = scope
        self.fields["connection"].queryset = SourceConnection.objects.filter(kind=kind).defer("credential")
        self.fields["connection"].required = True
        self.fields["submodule_connections"].queryset = SourceConnection.objects.filter(
            kind=SourceConnection.Kind.GIT,
        ).defer("credential")
        for field, default in HEALTH_DEFAULTS.items():
            self.fields[field].required = False
            self.fields[field].widget.attrs["placeholder"] = default
        if kind == SourceConnection.Kind.GIT:
            self.fields.pop("image")
        else:
            self.fields.pop("repository")
            self.fields.pop("revision")
            self.fields.pop("build_target")
            self.fields.pop("submodule_connections")
        if self.instance.pk:
            self.fields["slug"].disabled = True
            self.initial["tags"] = ", ".join(self.instance.tags.values_list("name", flat=True))

    def clean_tags(self):
        tags = list(dict.fromkeys(t.strip().lower() for t in self.cleaned_data["tags"].split(",") if t.strip()))
        if len(tags) > 20 or any(not TAG.fullmatch(tag) for tag in tags):
            raise forms.ValidationError("Use up to 20 lowercase tags containing letters, numbers or hyphens.")
        return tags

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            instance.tags.set([Tag.objects.get_or_create(name=name)[0] for name in self.cleaned_data["tags"]])
        return instance

    def clean_volumes(self):
        volumes = self.cleaned_data["volumes"]
        if volumes is None:
            return []
        if not isinstance(volumes, list) or len(volumes) > 16:
            raise forms.ValidationError("Enter a JSON list of up to 16 named mounts.")
        targets = set()
        for item in volumes:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not TAG.fullmatch(item["name"]):
                raise forms.ValidationError("Each mount needs a name like app-data.")
            target, read_only = item.get("target"), item.get("read_only", False)
            is_path = isinstance(target, str) and target.startswith("/") and not target.startswith("//")
            if (not is_path or target == "/" or "\0" in target or ".." in PurePosixPath(target).parts
                    or str(PurePosixPath(target)) != target or target.startswith("/run/secrets")
                    or target in targets or not isinstance(read_only, bool)):
                raise forms.ValidationError("Use unique absolute container paths (not / or /run/secrets) and true/false for read_only.")
            targets.add(target)
        return volumes

    def clean_slug(self):
        slug = self.cleaned_data["slug"]
        if self.scope.services.filter(slug=slug).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A service with this name already exists in this environment.")
        return slug

    def clean_secret_names(self):
        names = self.cleaned_data["secret_names"]
        if names is None:
            return []
        if not isinstance(names, list) or not all(
            isinstance(name, str) and SECRET.fullmatch(name) for name in names
        ):
            raise forms.ValidationError("Use a JSON list of Swarm secret names.")
        return list(dict.fromkeys(names))

    def clean(self):
        values = super().clean()
        connection = values.get("connection")
        git = self.kind == SourceConnection.Kind.GIT
        field = "repository" if git else "image"
        if field in values:
            try:
                authority = (repository_host if git else image_host)(values[field])
                if connection and authority != connection.host:
                    self.add_error(field, f"The source host must match {connection.host}.")
            except ValueError as exc:
                self.add_error(field, str(exc))
        if git and not re.fullmatch(r"[a-fA-F0-9]{40}", values.get("revision", "")):
            self.add_error("revision", "Use a full 40-character Git commit.")
        if git:
            target = values.get("build_target", "")
            if target and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", target):
                self.add_error("build_target", "Enter a Dockerfile stage name.")
            hosts = [item.host for item in values.get("submodule_connections", [])]
            if len(hosts) != len(set(hosts)) or (connection and connection.host in hosts):
                self.add_error("submodule_connections", "Choose at most one connection per additional Git host.")
        for field, default in HEALTH_DEFAULTS.items():
            if field in values and values[field] is None:
                values[field] = default
        return values
