"""Django settings for Waggle.

Everything is configured through ``WAGGLE_*`` environment variables so the same
image runs in development, in CI and in production without edits.
"""

from __future__ import annotations

import os
from pathlib import Path

from apps.core.version import VERSION

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
LOCK_DIR = DATA_DIR / "locks"
for _path in (DATA_DIR, LOG_DIR, BUILD_LOG_DIR, LOCK_DIR):
    _path.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #

SECRET_KEY = env("WAGGLE_SECRET_KEY", "dev-insecure-key-change-me")

#: Ordered key ring. The first key encrypts new values; every key can decrypt.
#: Add a new key first and keep the old one while data is re-encrypted.
SECRET_KEYS = tuple(env_list("WAGGLE_SECRET_KEYS")) or (SECRET_KEY,)
if SECRET_KEY not in SECRET_KEYS:
    SECRET_KEYS = (*SECRET_KEYS, SECRET_KEY)

DEBUG = env_bool("WAGGLE_DEBUG", False)
ALLOWED_HOSTS = env_list("WAGGLE_ALLOWED_HOSTS", ("*",) if DEBUG else ())
CSRF_TRUSTED_ORIGINS = env_list("WAGGLE_CSRF_TRUSTED_ORIGINS")

APP_VERSION = VERSION

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_cotton",
    "apps.core",
    "apps.servers",
    "apps.deployments",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
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
# Kept on the data volume so the control-plane container can run read-only.
STATIC_ROOT = DATA_DIR / "staticfiles"
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

#: Registry authority (host[:port]) workers can resolve and trust.
REGISTRY_HOST = env("WAGGLE_REGISTRY_HOST", "")
BUILD_TIMEOUT = env_int("WAGGLE_BUILD_TIMEOUT", 900)
ROUTE_TLS = env_bool("WAGGLE_ROUTE_TLS", False)
TRAEFIK_CERT_RESOLVER = env("WAGGLE_TRAEFIK_CERT_RESOLVER", "")
METRICS_TOKEN = env("WAGGLE_METRICS_TOKEN", "")
WEB_TERMINAL = env_bool("WAGGLE_WEB_TERMINAL", False)

#: SSH defaults for node provisioning.
SSH_CONNECT_TIMEOUT = env_int("WAGGLE_SSH_CONNECT_TIMEOUT", 15)
SSH_COMMAND_TIMEOUT = env_int("WAGGLE_SSH_COMMAND_TIMEOUT", 900)

#: Huey runs inline in the web process when this is true. Tests use it; prod does not.
HUEY_IMMEDIATE = env_bool("WAGGLE_HUEY_IMMEDIATE", False)

#: How long an SSE log stream may stay open before the client reconnects.
SSE_STREAM_SECONDS = env_int("WAGGLE_SSE_STREAM_SECONDS", 600)

#: How long a deployment may take to reach its desired running task count.
DEPLOY_CONVERGE_TIMEOUT = env_int("WAGGLE_DEPLOY_CONVERGE_TIMEOUT", 180)

#: A first pull of a large image can take longer than the convergence timeout.
#: While Swarm reports tasks in ``preparing`` the rollout keeps waiting, up to
#: this many seconds, instead of treating the pull as a failed rollout.
DEPLOY_PULL_TIMEOUT = env_int("WAGGLE_DEPLOY_PULL_TIMEOUT", 1800)

#: How often the deployment log receives a pull/startup progress line.
DEPLOY_PROGRESS_INTERVAL = env_int("WAGGLE_DEPLOY_PROGRESS_INTERVAL", 10)

#: Roll an existing service back to its previous spec when a rollout never
#: becomes healthy. Disable only if the orchestrator handles this natively.
DEPLOY_AUTO_ROLLBACK = env_bool("WAGGLE_DEPLOY_AUTO_ROLLBACK", True)

#: JSON logs are easier to ship to Loki/CloudWatch/ELK in production.
LOG_JSON = env_bool("WAGGLE_LOG_JSON", False)

#: Optional Slack-compatible webhook for deployment results.
NOTIFY_WEBHOOK_URL = env("WAGGLE_NOTIFY_WEBHOOK_URL", "")

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
        "json": {"()": "apps.core.logging.JsonFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if LOG_JSON else "plain",
        },
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
