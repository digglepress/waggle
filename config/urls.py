from __future__ import annotations

from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/change/", auth_views.PasswordChangeView.as_view(
        template_name="registration/password_change.html", success_url="/",
    ), name="password_change"),
    path("", include("apps.core.urls")),
    path("", include("apps.servers.urls")),
    path("apps/", include("apps.deployments.urls")),
    path("api/v1/", include("apps.deployments.api_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
