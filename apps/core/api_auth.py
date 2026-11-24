"""Bearer-token authentication for the automation API."""

from __future__ import annotations

from functools import wraps

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import APIToken


def require_token(scope: str):
    """Reject a request without a valid token that carries ``scope``."""

    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            header = request.headers.get("Authorization", "")
            scheme, _, raw = header.partition(" ")
            if scheme.lower() != "bearer" or not raw.strip():
                return JsonResponse(
                    {"error": "A bearer token is required."},
                    status=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            token = APIToken.authenticate(raw.strip())
            if token is None:
                return JsonResponse(
                    {"error": "The token is invalid or disabled."},
                    status=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            if not token.allows(scope):
                return JsonResponse(
                    {"error": f"This token does not have the {scope!r} scope."},
                    status=403,
                )
            request.api_token = token
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
