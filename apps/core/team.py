import secrets

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.deployments.forms import StyledForm

from . import audit

User = get_user_model()


class TeamMemberForm(StyledForm, forms.ModelForm):
    class Meta:
        model = User
        fields = ("username", "email", "is_staff")
        labels = {"is_staff": "Operator access"}
        help_texts = {"is_staff": "Operators can manage deployments and servers. Viewers can inspect status."}


@login_required
@require_http_methods(["GET", "POST"])
def team(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    form = TeamMemberForm(request.POST if request.method == "POST" else None)
    password = ""
    if request.method == "POST" and form.is_valid():
        member = form.save(commit=False)
        password = secrets.token_urlsafe(24)
        member.set_password(password)
        member.save()
        audit.record(request, "team.create", target=member,
                     detail={"is_staff": member.is_staff, "is_active": member.is_active})
        form = TeamMemberForm()
    response = render(request, "core/team.html", {
        "form": form, "members": User.objects.order_by("username"), "password": password,
    })
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def update_team(request, pk):
    if not request.user.is_superuser:
        raise PermissionDenied
    member = get_object_or_404(User, pk=pk, is_superuser=False)
    action = request.POST.get("action")
    if action in {"operator", "viewer"}:
        member.is_staff = action == "operator"
        member.save(update_fields=["is_staff"])
    elif action in {"enable", "disable"}:
        member.is_active = action == "enable"
        member.save(update_fields=["is_active"])
    else:
        return redirect("core:team")
    audit.record(request, f"team.{action}", target=member,
                 detail={"is_staff": member.is_staff, "is_active": member.is_active})
    return redirect("core:team")
