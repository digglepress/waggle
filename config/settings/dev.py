"""Development settings: readable errors, permissive hosts, no TLS enforcement."""

from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["*"]
CSRF_TRUSTED_ORIGINS = []

# Compressed-but-not-manifest static storage keeps `runserver` painless.
STORAGES["staticfiles"] = {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}  # noqa: F405

INTERNAL_IPS = ["127.0.0.1"]
