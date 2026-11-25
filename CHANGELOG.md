# Changelog

All notable changes to Waggle are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and releases use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Deployments are pull-aware: while Swarm reports tasks in `preparing`, the
  rollout keeps waiting instead of treating a slow image pull as a failed
  rollout. The normal convergence timeout still applies once the pull finishes,
  and `WAGGLE_DEPLOY_PULL_TIMEOUT` (default 1800s) caps the wait for pulls.
- Deployment logs now report image-pull and task-startup progress, including
  the node names and elapsed time, so a slow first pull is visible in the UI.
- The service overview labels live domain links as **Open** actions.

### Fixed

- Rollouts no longer count tasks that Swarm is shutting down, so an update is
  not considered converged while only the old task is still running.
- When a slow pull finishes, the startup phase gets a fresh convergence window
  instead of being failed because the pull consumed the original timeout.

## [0.2.0] - 2026-11-23

### Added

- Digest-pinned, cosign-signed release images and a release workflow with
  SBOM, provenance and a critical-vulnerability scan.
- `deploy/upgrade.sh`: pre-upgrade database backup, signature verification,
  health-gated rollout and automatic rollback to the previous image.
- `deploy/backup.sh`, systemd service and timer for scheduled, compressed,
  checksummed and optionally `age`-encrypted SQLite backups.
- Deployment convergence: a deployment only succeeds when the requested tasks
  are running. Existing services are rolled back automatically when a rollout
  does not converge.
- Fail-closed production settings, `manage.py check --deploy` in CI, a
  `SecurityHeadersMiddleware` with CSP, request IDs, JSON logging and
  `/readyz`.
- Audit log for deployments, rollbacks, service edits, domain and variable
  changes, team changes, server removal, SSH keys, terminal commands and file
  browsing.
- `WAGGLE_SECRET_KEYS` key ring so encryption keys can be rotated without
  losing existing values.
- Cross-process operation locks for terminal and file-browser commands.
- Read-only control-plane containers with dropped capabilities,
  `no-new-privileges` and log rotation.
- Pinned runtime dependencies in `requirements.lock` and a digest-pinned base
  image.
- Scoped bearer-token API for listing services, reading deployment status,
  deploying and rolling back, plus a `create_api_token` management command.
- Offline installs from a checksummed image tarball and a reference Caddy
  configuration for control-plane TLS.
- `deploy/volume-backup.sh` for compressed, checksummed backup and verified
  restore of application volumes on the node that holds them.
- Core migrations for the audit log and API tokens.

### Changed

- The deployment success state is labelled **Deployed** instead of
  **Submitted to Swarm**. A new **Waiting for tasks** state covers the rollout.
- `STATIC_ROOT` moved under the data volume so the control plane can run with
  a read-only filesystem.
- `/healthz` no longer returns internal exception text.

### Fixed

- The default CSP blocked the inline theme bootstrap and toggle, the project
  view switch, and the server-removal confirmation. These are now external
  scripts, and the external font request was removed.
- The web UI is branded Waggle again; the theme preference uses a new storage
  key while still reading the previous one.
- The deployment page now updates its status badge when the log stream
  finishes.
- The dashboard counts running managed services instead of always showing
  zero.

### Security

- Production refuses to start with the development secret, a wildcard
  `ALLOWED_HOSTS`, or a too-short key.
- Secure cookies, HSTS and optional SSL redirect are configured from one
  place; the installer writes the matching environment variables.
- Docker and Compose installs default to verify-or-refuse for release images.

## [0.1.0] - 2026-11-22

Initial milestone release: Swarm orchestration, SSH provisioning, projects,
environments, services, builds, routing, logs, rollback, backups, team access,
volumes, health checks, domains, terminal and file browser.
