"""Runtime environment editors share a lossless literal format (no shell expansion)."""

import json
import re

from django import forms

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PLAIN_VALUE = re.compile(r"""[^\s#'"\\]*""")
MAX_VARIABLES = 200


def add_variable(values, name, value):
    if not NAME.fullmatch(name):
        raise ValueError("Use a variable name like APP_MODE.")
    if name in values:
        raise ValueError(f"Duplicate variable: {name}.")
    if "\0" in value or len(value) > 8192:
        raise ValueError("Values must be at most 8192 characters and cannot contain NUL.")
    if len(values) >= MAX_VARIABLES:
        raise ValueError(f"Use at most {MAX_VARIABLES} variables.")
    values[name] = value


def parse_dotenv(text):
    values = {}
    for index, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if not separator:
            raise ValueError(f"Line {index}: expected NAME=value.")
        value = value.strip()
        try:
            if value.startswith('"'):
                value, end = json.JSONDecoder().raw_decode(value)
                rest = line.partition("=")[2].strip()[end:].strip()
                if rest and not rest.startswith("#"):
                    raise ValueError("Unexpected text after the quoted value.")
            elif value.startswith("'"):
                value, end, rest = value[1:].partition("'")
                if not end or (rest.strip() and not rest.strip().startswith("#")):
                    raise ValueError("Invalid single-quoted value.")
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            add_variable(values, name.strip(), value)
        except json.JSONDecodeError:
            raise ValueError(f"Line {index}: invalid quoted value.") from None
        except ValueError as exc:
            raise ValueError(f"Line {index}: {exc}") from None
    return values


def format_dotenv(values):
    return "\n".join(
        f"{key}={value if PLAIN_VALUE.fullmatch(value) else json.dumps(value)}"
        for key, value in values.items()
    )


class TextVariablesForm(forms.Form):
    variables = forms.CharField(
        label="Environment variables", required=False, strip=False, max_length=250_000,
        widget=forms.Textarea(attrs={"class": "input mono", "rows": 14, "spellcheck": "false"}),
    )

    def clean_variables(self):
        try:
            return parse_dotenv(self.cleaned_data["variables"])
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc


class VariableForm(forms.Form):
    name = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"class": "input mono"}))
    value = forms.CharField(required=False, strip=False, max_length=8192, widget=forms.Textarea(
        attrs={"class": "input mono", "rows": 2},
    ))


class VariableFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        values = {}
        for form in self.forms:
            row = form.cleaned_data
            if not row or row.get("DELETE"):
                continue
            try:
                add_variable(values, row["name"], row["value"])
            except ValueError as exc:
                form.add_error("name", str(exc))
        self.variables = values


VariablesFormSet = forms.formset_factory(
    VariableForm, formset=VariableFormSet, extra=1, can_delete=True,
    max_num=MAX_VARIABLES, validate_max=True, absolute_max=MAX_VARIABLES + 1,
)
