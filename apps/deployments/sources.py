"""Canonical source hosts shared by form and deployment validation."""

import re
from urllib.parse import urlsplit

HOST = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?")
IMAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]*")


def source_host(value):
    value = value.strip().lower()
    if not HOST.fullmatch(value):
        raise ValueError("Enter a hostname and optional port, without a scheme or path.")
    if ":" in value and not 1 <= int(value.rsplit(":", 1)[1]) <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return value


def registry_host(value):
    host = source_host(value)
    return "docker.io" if host in {"index.docker.io", "registry-1.docker.io"} else host


def image_host(image):
    if not IMAGE.fullmatch(image) or "://" in image:
        raise ValueError("Enter a Docker image reference without a URL scheme.")
    head, slash, _ = image.partition("/")
    if slash and ("." in head or ":" in head or head.lower() == "localhost"):
        return registry_host(head)
    return "docker.io"


def repository_host(repository):
    try:
        url = urlsplit(repository)
        host = source_host(url.netloc)
        if (url.scheme != "https" or url.username is not None or url.query or url.fragment
                or not url.path.endswith(".git")):
            raise ValueError
        return host
    except ValueError:
        raise ValueError("Use an HTTPS Git URL ending in .git, without embedded credentials.") from None
