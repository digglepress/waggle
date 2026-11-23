from django.urls import path

from apps.deployments import terminal_views, views

app_name = "deployments"

urlpatterns = [
    path("", views.index, name="index"),
    path("projects/new/", views.new_project, name="new_project"),
    path("projects/<int:pk>/", views.project, name="project"),
    path("projects/<int:project_id>/environments/new/", views.new_environment, name="new_environment"),
    path("environments/<int:pk>/", views.environment, name="environment"),
    path("environments/<int:env_id>/new/", views.choose_source, name="choose_source"),
    path("environments/<int:env_id>/new/<str:kind>/", views.edit, name="new"),
    path("connections/", views.connections, name="connections"),
    path("connections/new/<str:kind>/", views.new_connection, name="new_connection"),
    path("connections/<int:pk>/edit/", views.edit_connection, name="edit_connection"),
    path("notifications/", views.notifications, name="notifications"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.edit, name="edit"),
    path("<int:pk>/domains/", views.domains, name="domains"),
    path("<int:pk>/deploy/", views.launch, name="launch"),
    path("<int:pk>/rollback/", views.revert, name="rollback"),
    path("<int:pk>/history/", views.history, name="history"),
    path("<int:pk>/containers/", views.tasks, name="tasks"),
    path("<int:pk>/terminal/", terminal_views.terminal, name="terminal"),
    path("<int:pk>/terminal/execute/", terminal_views.execute, name="terminal_execute"),
    path("<int:pk>/files/", terminal_views.files, name="files"),
    path("<int:pk>/files/browse/", terminal_views.browse_files, name="browse_files"),
    path("<int:pk>/history/<int:job_id>/rollback/", views.revert_to, name="rollback_to"),
    path("<int:pk>/logs/", views.logs, name="logs"),
    path("<int:pk>/logs/stream/", views.runtime_stream, name="runtime_stream"),
    path("<int:pk>/variables/", views.variables, name="variables"),
    path("<int:pk>/deployments/<int:job_id>/", views.job, name="job"),
    path("<int:pk>/deployments/<int:job_id>/stream/", views.deployment_stream, name="deployment_stream"),
]
