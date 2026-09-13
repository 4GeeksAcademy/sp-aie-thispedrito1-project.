# Contenedor scheduler dedicado para el job nocturno (Ticket #DEV-53).
#
# Separado de `api` a propósito: el job no puede compartir proceso ni hilo con
# FastAPI. Usa la misma imagen base y las mismas dependencias de Python que
# services/api (el pipeline que lanza necesita Prefect, Pandas y SQLModel).
#
# supercronic en vez del cron de Debian: pasa las variables de entorno del
# contenedor al job (cron las borra) y escribe los logs en stdout, que es
# donde `docker compose logs scheduler` los busca.
#
# Contexto de build: la raíz del repo (ver docker-compose.yml). El archivo
# Dockerfile.dockerignore de al lado limita lo que se envía a Docker.
FROM python:3.12-slim

ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA256_AMD64=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1
ARG SUPERCRONIC_SHA256_ARM64=02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5

# libpq5: mismo motivo que en services/api/Dockerfile (psycopg2-binary).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "${TARGETARCH:-amd64}" in \
      amd64) sha="${SUPERCRONIC_SHA256_AMD64}" ;; \
      arm64) sha="${SUPERCRONIC_SHA256_ARM64}" ;; \
      *) echo "arquitectura no soportada: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH:-amd64}"; \
    echo "${sha}  /usr/local/bin/supercronic" | sha256sum -c -; \
    chmod +x /usr/local/bin/supercronic

RUN pip install --no-cache-dir uv
COPY services/api/requirements.txt /tmp/requirements.txt
RUN uv pip install --system --no-cache -r /tmp/requirements.txt

WORKDIR /app
# El código llega por bind mount (docker-compose.yml), como en los demás
# servicios de desarrollo. La copia del crontab es para que la imagen
# funcione también sin montajes.
COPY services/scheduler/crontab /app/services/scheduler/crontab

CMD ["supercronic", "/app/services/scheduler/crontab"]
