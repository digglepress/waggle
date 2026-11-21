from __future__ import annotations

from django.contrib import admin

from .models import Server, SSHKey


@admin.register(SSHKey)
class SSHKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "role",
        "status",
        "availability",
        "address",
        "is_local",
        "is_builder",
        "last_seen",
    )
    list_filter = ("role", "status", "availability", "is_local")
    search_fields = ("name", "address", "swarm_node_id")
    readonly_fields = (
        "swarm_node_id",
        "cluster_id",
        "ssh_host_key",
        "bootstrap_step",
        "created_at",
        "updated_at",
        "last_seen",
    )
    fieldsets = (
        (None, {"fields": ("name", "address", "ssh_port", "ssh_user", "ssh_key", "is_local")}),
        ("Role", {"fields": ("role", "is_builder", "advertise_addr", "labels")}),
        ("Observed", {"fields": ("status", "availability", "swarm_node_id", "cluster_id")}),
        (
            "Host",
            {
                "fields": (
                    "os_name",
                    "arch",
                    "cpu_cores",
                    "ram_mb",
                    "disk_gb",
                    "docker_version",
                    "last_seen",
                )
            },
        ),
        ("Diagnostics", {"fields": ("bootstrap_step", "bootstrap_error", "last_error", "ssh_host_key")}),
    )
