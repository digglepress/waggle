"""Production settings.

The control plane sits behind Traefik, which terminates TLS, so we trust the
``X-Forwarded-Proto`` header and keep cookies secure.
"""

from .base import *  # noqa: F403

DEBUG = False

if not ALLOWED_HOSTS:  # noqa: F405
    raise RuntimeError("WAGGLE_ALLOWED_HOSTS must be set when WAGGLE_DEBUG=0")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

#: True once TLS terminates in front of Waggle. M1's installer serves plain
#: HTTP on :8000 and sets this to 0; flip it when Traefik lands in M2, or set
#: WAGGLE_SECURE_COOKIES=1 yourself.
SECURE_COOKIES = env_bool("WAGGLE_SECURE_COOKIES", True)  # noqa: F405
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES

if SECURE_COOKIES:
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

# Manifest storage gives us far-future cache headers for static assets.
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
}

LOGGING["root"]["level"] = "INFO"  # noqa: F405
