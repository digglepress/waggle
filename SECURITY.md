# Security

## Threat model

Waggle is a **single-tenant** control plane for infrastructure you own. Do not
give untrusted users accounts. Staff users can deploy arbitrary containers and
build arbitrary Dockerfiles; the server provisioning UI is also privileged.

Two properties matter more than anything else:

1. **`/var/run/docker.sock` is root.** The control plane container mounts the
   manager's Docker socket so it can create services. Compromising the web
   process is equivalent to compromising the manager. The optional Traefik
   service also has manager API access; a read-only socket mount is not a
   Docker API permission boundary. Never mount the socket into an application.
2. **SSH private keys are stored in the database.** They are encrypted at rest
   with Fernet, keyed from `WAGGLE_SECRET_KEY` / `WAGGLE_SECRET_KEYS`. Treat
   the database file (`data/waggle.sqlite3`) and the secret keys as equally
   sensitive. `WAGGLE_SECRET_KEYS` is an ordered key ring: new values use the
   first key, older keys still decrypt. Prepend a new key and keep the old one
   until every stored value has been rewritten; removing it makes old values
   unreadable by design.

## Releases

- Production installs use a digest-pinned image, never `latest` or a branch.
  The installer refuses an unpinned reference unless `WAGGLE_ALLOW_UNPINNED=1`.
- Release images are signed with cosign from the release workflow. Set
  `WAGGLE_REQUIRE_SIGNATURE=1` and `WAGGLE_COSIGN_IDENTITY` so the installer
  and upgrader verify the signature before pulling. Verify manually with the
  identity printed in the release notes.
- `deploy/upgrade.sh` backs up the database first and rolls back to the
  previous image when the new one fails `/readyz`.
- Runtime dependencies are pinned in `requirements.lock`; CI audits them and
  scans the built image for critical and high vulnerabilities.

## Before exposing Waggle to the internet

The installer binds plain HTTP to localhost on port 8000 by default and sets
`WAGGLE_SECURE_COOKIES=0` until you add TLS. Before exposing it:

1. Put a TLS-terminating proxy in front of the control plane and set
   `WAGGLE_SECURE_COOKIES=1`. The optional Traefik stack routes applications,
   not the control plane by default.
2. Set `WAGGLE_ALLOWED_HOSTS` to the real hostname instead of `*`.
3. Set `WAGGLE_CSRF_TRUSTED_ORIGINS` to `https://your.host`.
4. Restrict port 8000 to the proxy, or bind it to localhost
   (`WAGGLE_HTTP_PORT=127.0.0.1:8000`).
5. Keep `deploy/.env` at mode `0600` and out of version control.
6. Back up `data/waggle.sqlite3` and `WAGGLE_SECRET_KEY` together, offline.
7. Set `WAGGLE_SSL_REDIRECT=1` once TLS is the only public entry point, and
   keep `WAGGLE_SECURE_COOKIES=1` so sessions and CSRF cookies are `Secure`.
8. Keep `WAGGLE_METRICS_TOKEN` unset unless Prometheus scrapes over TLS or a
   private network; the endpoint is disabled without a token.
9. Review the **Audit log** after granting access or removing a server. Audit
   entries omit secret values and command output by design.

The Compose services run with `no-new-privileges`, all capabilities dropped,
a read-only root filesystem and a writable `/tmp` tmpfs. That limits a web
process bug, but it does not make Docker socket access safe: socket access is
still root-equivalent on the manager.

## Backups

`deploy/backup.sh` writes an online SQLite snapshot, compresses it, records a
SHA-256 checksum and prunes old copies. Set `WAGGLE_BACKUP_AGE_RECIPIENT` to
encrypt with `age` before the file leaves the host. Keep the backup and the
secret key separately, and test a restore into a fresh data directory. A copy
of the database without the matching `WAGGLE_SECRET_KEY` cannot decrypt stored
SSH keys, source credentials or environment values.

## Automation API

- API tokens are bearer credentials. Use HTTPS, keep them in a CI secret
  store, and prefer a `read` token for dashboards and a separate `deploy`
  token for pipelines.
- Only a SHA-256 digest is stored. Revoke a token with
  `manage.py create_api_token --revoke PREFIX`; revocation is immediate.
- The API cannot read environment values, secrets, logs or files, and it does
  not bypass the operator-only terminal or file browser.
- Every API deployment is recorded in the audit log with the token name, not
  the token value.

## Provisioning

- SSH host keys are pinned on first use (trust on first use) and stored on the
  `Server` row. A changed host key is reported as a bootstrap failure rather
  than silently accepted.
- Waggle never stores SSH passwords. The target user needs passwordless `sudo`.
- Restrict SSH on worker nodes to the control plane's address, and use a
  dedicated account rather than a shared root login.
- Swarm join tokens are fetched on demand and passed on the command line to
  `docker swarm join`. They are not persisted.

## Applications and registry

- Build and run only trusted code. The manager runs builds, and apps on the
  shared proxy overlay can contact one another.
- Keep secrets out of environment variables, Dockerfiles, build logs, and
  Git URLs. Mount an existing Swarm secret instead.
- Environment values and deployment snapshots are encrypted with
  `WAGGLE_SECRET_KEY` in new database writes and migrated rows. This does
  not encrypt the Swarm service spec, historical backups made before the
  migration, or previous copies of the SQLite WAL.
- Keep registry TLS keys, password files, and Docker credentials outside the
  checkout and build context. Restrict registry access to the private cluster.
- The three-container lab uses privileged Docker-in-Docker nodes. Use it only
  on a trusted local machine; it is not a security boundary for untrusted code.
  Its generated admin password is kept in a private manager volume for local
  retrieval. If you change the password, that file still contains the old one.
- Treat the browser Terminal and Files pages as privileged access. They
  are opt-in outside the lab, require an operator account, and check the
  target node, task and container. Use TLS, avoid entering credentials in
  commands, and do not expose them to untrusted operators or workloads.

## Reporting a vulnerability

Please email the maintainers rather than opening a public issue. Include the
version (`/healthz` and the footer), a description, and reproduction steps.
