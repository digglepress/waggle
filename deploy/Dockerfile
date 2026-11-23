# syntax=docker/dockerfile:1

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod \
    WAGGLE_DATA_DIR=/var/lib/waggle

ARG TARGETARCH=amd64
ARG DOCKER_VERSION=27.5.1
ARG BUILDX_VERSION=v0.20.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git tini \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI + buildx. The control plane talks to the host daemon through the
# mounted socket; it never runs a daemon of its own.
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) DOCKER_ARCH=x86_64; BUILDX_ARCH=amd64 ;; \
        arm64) DOCKER_ARCH=aarch64; BUILDX_ARCH=arm64 ;; \
        *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-${DOCKER_VERSION}.tgz" -o /tmp/docker.tgz; \
    tar -xzf /tmp/docker.tgz -C /tmp; \
    install -m 0755 /tmp/docker/docker /usr/local/bin/docker; \
    mkdir -p /usr/libexec/docker/cli-plugins; \
    curl -fsSL "https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}/buildx-${BUILDX_VERSION}.linux-${BUILDX_ARCH}" \
        -o /usr/libexec/docker/cli-plugins/docker-buildx; \
    chmod 0755 /usr/libexec/docker/cli-plugins/docker-buildx; \
    rm -rf /tmp/docker /tmp/docker.tgz

WORKDIR /app

COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY . .
RUN chmod +x deploy/entrypoint.sh

ARG WAGGLE_VERSION=dev
LABEL org.opencontainers.image.title="Waggle" \
      org.opencontainers.image.description="Swarm-native deployment control plane" \
      org.opencontainers.image.version="${WAGGLE_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--", "/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--worker-class", "gthread", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
