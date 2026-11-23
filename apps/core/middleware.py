"""Small, dependency-free security and request-correlation middleware."""

from __future__ import annotations

import uuid

CSP = "; ".join((
    "default-src 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "img-src 'self' data:",
    "font-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
))
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


class RequestIDMiddleware:
    """Attach a request ID to the request and response for logs and audits."""

    header = "HTTP_X_REQUEST_ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.META.get(self.header, "").strip()
        request.request_id = (
            supplied if supplied.isascii() and supplied.isalnum() and len(supplied) <= 64
            else uuid.uuid4().hex
        )
        response = self.get_response(request)
        response.setdefault("X-Request-ID", request.request_id)
        return response


class SecurityHeadersMiddleware:
    """Headers that Django's security middleware does not set by itself."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", CSP)
        response.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.setdefault("X-Content-Type-Options", "nosniff")
        return response
