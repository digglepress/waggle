# Changelog

All notable changes to Waggle are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and releases use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
