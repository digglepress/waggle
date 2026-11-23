import re

from django import forms
from django.db import transaction

from apps.deployments.models import ServiceDomain

LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOSTNAME = re.compile(rf"(?:{LABEL}\.)+{LABEL}")
HOST_RULE = re.compile(r"Host\(`([a-z0-9.-]+)`\)", re.IGNORECASE)
MAX_DOMAINS = 20


class DomainError(ValueError):
    pass


def normalize_hostname(value):
    if not isinstance(value, str):
        raise DomainError("A hostname must be text.")
    hostname = value.strip().lower().removesuffix(".")
    if len(hostname) > 253 or not HOSTNAME.fullmatch(hostname):
        raise DomainError("Enter DNS hostnames without a scheme, port, path, or wildcard.")
    return hostname


def requested_domains(values):
    """New snapshots use a list; older deployment history saved one hostname."""
    candidates = values.get("domains")
    if candidates is None:
        candidates = [values["domain"]] if values.get("domain") else []
    if not isinstance(candidates, list) or len(candidates) > MAX_DOMAINS:
        raise DomainError(f"Use at most {MAX_DOMAINS} domains per service.")
    return list(dict.fromkeys(normalize_hostname(candidate) for candidate in candidates))


def service_hostnames(labels):
    if labels.get("waggle.domains"):
        names = labels["waggle.domains"].split(",")
    else:
        names = [
            hostname for key, value in labels.items()
            if key.startswith("traefik.http.routers.") and key.endswith(".rule")
            for hostname in HOST_RULE.findall(value)
        ]
    return {name.lower() for name in names if HOSTNAME.fullmatch(name.lower())}


def service_tls_enabled(labels):
    return any(
        key.startswith("traefik.http.routers.") and key.endswith(".tls") and value.lower() == "true"
        for key, value in labels.items()
    )


def validate_routing(orchestrator, app, domains):
    if not domains:
        return
    assigned = set(app.domains.filter(hostname__in=domains).values_list("hostname", flat=True))
    unassigned = sorted(set(domains) - assigned)
    if unassigned:
        raise DomainError(f"Assign {', '.join(unassigned)} to this service in Domains before deploying or rolling back.")
    for service in orchestrator.list_services():
        if service.labels.get("waggle.app-id") == str(app.pk):
            continue
        active = sorted(set(domains) & service_hostnames(service.labels))
        if active:
            raise DomainError(f"{', '.join(active)} is still routed by {service.name}. Remove the domain there and redeploy first.")


class DomainsForm(forms.Form):
    hostnames = forms.CharField(
        required=False, max_length=6000, label="Hostnames",
        widget=forms.Textarea(attrs={"class": "input mono", "rows": 6, "spellcheck": "false",
                                     "placeholder": "api.example.org\napi-alt.example.org"}),
    )

    def __init__(self, *args, app, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)
        self.initial.setdefault("hostnames", "\n".join(app.domains.values_list("hostname", flat=True)))

    def clean_hostnames(self):
        try:
            domains = requested_domains({
                "domains": [line for line in self.cleaned_data["hostnames"].splitlines() if line.strip()],
            })
        except DomainError as exc:
            raise forms.ValidationError(str(exc)) from exc
        conflict = (
            ServiceDomain.objects.filter(hostname__in=domains).exclude(application=self.app)
            .values_list("hostname", flat=True).first()
        )
        if conflict:
            raise forms.ValidationError(f"{conflict} is already assigned to another service.")
        return domains

    @transaction.atomic
    def save(self):
        hostnames = self.cleaned_data["hostnames"]
        current = set(self.app.domains.values_list("hostname", flat=True))
        self.app.domains.exclude(hostname__in=hostnames).delete()
        ServiceDomain.objects.bulk_create([
            ServiceDomain(application=self.app, hostname=hostname)
            for hostname in hostnames if hostname not in current
        ])
