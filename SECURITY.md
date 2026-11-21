# Security

## Threat model

Waggle is a **single-tenant** control plane for infrastructure you own. It is not
hardened for untrusted users: anyone who can sign in can ask Waggle to run
arbitrary containers on your servers.

Two properties matter more than anything else:

1. **`/var/run/docker.sock` is root.** The control plane container mounts the
   manager's Docker socket so it can create services. Compromising the web
   process is equivalent to compromising the manager. Do not mount the socket
   into anything else, and do not run untrusted code in the control plane.
2. **SSH private keys are stored in the database.** They are encrypted at rest
   with Fernet, keyed from `WAGGLE_SECRET_KEY`. Treat the database file
   (`data/waggle.sqlite3`) and the secret key as equally sensitive.

## Before exposing Waggle to the internet

The M1 installer serves plain HTTP on port 8000 and sets
`WAGGLE_SECURE_COOKIES=0`. That is fine on a private network, not on the public
internet. Before exposing it:

1. Put a TLS-terminating proxy in front of the control plane (Traefik arrives
   bundled in M2) and set `WAGGLE_SECURE_COOKIES=1`.
2. Set `WAGGLE_ALLOWED_HOSTS` to the real hostname instead of `*`.
3. Set `WAGGLE_CSRF_TRUSTED_ORIGINS` to `https://your.host`.
4. Restrict port 8000 to the proxy, or bind it to localhost
   (`WAGGLE_HTTP_PORT=127.0.0.1:8000`).
5. Keep `deploy/.env` at mode `0600` and out of version control.
6. Back up `data/waggle.sqlite3` and `WAGGLE_SECRET_KEY` together, offline.

## Provisioning

- SSH host keys are pinned on first use (trust on first use) and stored on the
  `Server` row. A changed host key is reported as a bootstrap failure rather
  than silently accepted.
- Waggle never stores SSH passwords. The target user needs passwordless `sudo`.
- Restrict SSH on worker nodes to the control plane's address, and use a
  dedicated account rather than a shared root login.
- Swarm join tokens are fetched on demand and passed on the command line to
  `docker swarm join`. They are not persisted.

## Reporting a vulnerability

Please email the maintainers rather than opening a public issue. Include the
version (`/healthz` and the footer), a description, and reproduction steps.
