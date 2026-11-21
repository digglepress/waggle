"""Django settings for Waggle.

Everything is configured through ``WAGGLE_*`` environment variables so the same
image runs in development, in CI and in production without edits.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: tuple[str, ...] = ()) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

DATA_DIR = Path(env("WAGGLE_DATA_DIR", str(BASE_DIR / "data"))).resolve()
LOG_DIR = DATA_DIR / "logs"
BUILD_LOG_DIR = LOG_DIR / "deployments"
for _path in (DATA_DIR, LOG_DIR, BUILD_LOG_DIR):
    _path.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #

SECRET_KEY = env("WAGGLE_SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = env_bool("WAGGLE_DEBUG", False)
ALLOWED_HOSTS = env_list("WAGGLE_ALLOWED_HOSTS", ("*",) if DEBUG else ())
CSRF_TRUSTED_ORIGINS = env_list("WAGGLE_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.servers",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.waggle",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --------------------------------------------------------------------------- #
# Database: SQLite in WAL mode. No Postgres, no Redis.
# --------------------------------------------------------------------------- #

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "waggle.sqlite3",
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA busy_timeout=20000;"
                "PRAGMA foreign_keys=ON;"
            ),
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# --------------------------------------------------------------------------- #
# I18N / static
# --------------------------------------------------------------------------- #

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# --------------------------------------------------------------------------- #
# Waggle
# --------------------------------------------------------------------------- #

#: Dotted path to the orchestrator implementation. Tests swap in the fake.
ORCHESTRATOR = env("WAGGLE_ORCHESTRATOR", "orchestrator.swarm:SwarmOrchestrator")

#: Optional Docker endpoint. Empty means "use docker.from_env()".
DOCKER_URL = env("WAGGLE_DOCKER_URL") or None
DOCKER_TIMEOUT = env_int("WAGGLE_DOCKER_TIMEOUT", 30)

#: Overlay network shared by the edge proxy and every application.
PROXY_NETWORK = env("WAGGLE_PROXY_NETWORK", "paas_proxy")

#: Prefix for every Docker object Waggle creates. Lets Waggle own its namespace.
OBJECT_PREFIX = env("WAGGLE_OBJECT_PREFIX", "waggle")

#: Default image registry. Populated in M2 when the deploy pipeline lands.
REGISTRY_HOST = env("WAGGLE_REGISTRY_HOST", "")

#: SSH defaults for node provisioning.
SSH_CONNECT_TIMEOUT = env_int("WAGGLE_SSH_CONNECT_TIMEOUT", 15)
SSH_COMMAND_TIMEOUT = env_int("WAGGLE_SSH_COMMAND_TIMEOUT", 900)

#: Huey runs inline in the web process when this is true. Tests use it; prod does not.
HUEY_IMMEDIATE = env_bool("WAGGLE_HUEY_IMMEDIATE", False)

#: How long an SSE log stream may stay open before the client reconnects.
SSE_STREAM_SECONDS = env_int("WAGGLE_SSE_STREAM_SECONDS", 600)

#: Minimum RAM (MB) a node needs before we stop complaining.
NODE_MIN_RAM_MB = env_int("WAGGLE_NODE_MIN_RAM_MB", 1024)

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)-7s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "waggle.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "plain",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "waggle": {"handlers": ["file"], "level": "INFO", "propagate": True},
        "django.request": {"level": "WARNING"},
    },
}
