from __future__ import annotations

from django import forms

from .models import Server, SSHKey

RESERVED_LABEL_PREFIX = "waggle."


class StyledFormMixin:
    """Add the shared ``.input`` class to every widget except checkboxes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} input".strip()


class SSHKeyForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SSHKey
        fields = ["name", "private_key", "public_key"]
        widgets = {
            "private_key": forms.Textarea(
                attrs={
                    "rows": 8,
                    "class": "mono",
                    "placeholder": "-----BEGIN OPENSSH PRIVATE KEY-----",
                    "autocomplete": "off",
                }
            ),
            "public_key": forms.Textarea(
                attrs={"rows": 3, "class": "mono", "placeholder": "ssh-ed25519 AAAA..."}
            ),
        }

    def clean_private_key(self) -> str:
        value = self.cleaned_data["private_key"].strip()
        if "PRIVATE KEY" not in value:
            raise forms.ValidationError("That does not look like a private key.")
        return value + "\n"


class ServerForm(StyledFormMixin, forms.ModelForm):
    labels = forms.JSONField(
        required=False,
        initial=dict,
        widget=forms.Textarea(attrs={"rows": 4, "class": "mono"}),
        help_text='JSON object of node labels, for example {"zone": "eu-west"}.',
    )

    class Meta:
        model = Server
        fields = [
            "name",
            "address",
            "ssh_port",
            "ssh_user",
            "ssh_key",
            "role",
            "advertise_addr",
            "labels",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "edge-1"}),
            "address": forms.TextInput(attrs={"placeholder": "203.0.113.10"}),
            "advertise_addr": forms.TextInput(attrs={"placeholder": "10.0.0.10 (optional)"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ssh_key"].queryset = SSHKey.objects.all()
        self.fields["ssh_key"].required = True
        self.fields["ssh_key"].empty_label = "Select an SSH key"

    def clean_address(self) -> str:
        return self.cleaned_data["address"].strip()

    def clean_labels(self) -> dict[str, str]:
        labels = self.cleaned_data.get("labels") or {}
        if not isinstance(labels, dict):
            raise forms.ValidationError("Labels must be a JSON object.")
        cleaned: dict[str, str] = {}
        for key, value in labels.items():
            if not isinstance(key, str) or not key.strip():
                raise forms.ValidationError("Label keys must be non-empty strings.")
            if not isinstance(value, str):
                raise forms.ValidationError(f"Label '{key}' must have a string value.")
            if key.startswith(RESERVED_LABEL_PREFIX):
                raise forms.ValidationError(f"The '{RESERVED_LABEL_PREFIX}' prefix is reserved.")
            cleaned[key.strip()] = value
        return cleaned

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("ssh_key"):
            self.add_error("ssh_key", "An SSH key is required to bootstrap a server.")
        return cleaned
