"""Production settings.

The control plane sits behind Traefik, which terminates TLS, so we trust the
``X-Forwarded-Proto`` header and keep cookies secure. Every dangerous default
fails at import time rather than silently serving production traffic.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False

if SECRET_KEY == "dev-insecure-key-change-me" or len(SECRET_KEY) < 32:  # noqa: F405
    raise ImproperlyConfigured(
        "Set WAGGLE_SECRET_KEY to a random value of at least 32 characters."
    )
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured(
        "Set WAGGLE_ALLOWED_HOSTS to the public hostname(s); '*' is not allowed in production."
    )
if any(not key or len(key) < 16 for key in SECRET_KEYS):  # noqa: F405
    raise ImproperlyConfigured("Every WAGGLE_SECRET_KEYS entry must be at least 16 characters.")

SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https") if env_bool("WAGGLE_TRUST_PROXY", True) else None  # noqa: F405
)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = env_int("WAGGLE_SESSION_AGE", 12 * 60 * 60)  # noqa: F405
X_FRAME_OPTIONS = "DENY"

#: True once TLS terminates in front of Waggle. Set WAGGLE_SECURE_COOKIES=0
#: only while the UI is bound to localhost and reached through an SSH tunnel.
SECURE_COOKIES = env_bool("WAGGLE_SECURE_COOKIES", True)  # noqa: F405
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES
SECURE_SSL_REDIRECT = env_bool("WAGGLE_SSL_REDIRECT", True)  # noqa: F405
if SECURE_SSL_REDIRECT and not SECURE_COOKIES:
    raise ImproperlyConfigured("WAGGLE_SSL_REDIRECT=1 requires WAGGLE_SECURE_COOKIES=1.")

if SECURE_COOKIES:
    SECURE_HSTS_SECONDS = env_int("WAGGLE_HSTS_SECONDS", 60 * 60 * 24 * 365)  # noqa: F405
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool(  # noqa: F405
        "WAGGLE_HSTS_INCLUDE_SUBDOMAINS", False,
    )
    SECURE_HSTS_PRELOAD = env_bool("WAGGLE_HSTS_PRELOAD", False)  # noqa: F405
else:
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

# Manifest storage gives us far-future cache headers for static assets.
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
}

LOGGING["root"]["level"] = "INFO"  # noqa: F405
