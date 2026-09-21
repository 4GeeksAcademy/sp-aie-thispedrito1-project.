# Imagen del worker de Celery y de Flower (Ticket #DEV-55).
#
# Proceso aparte de `api` a propósito: el ticket exige que las tareas no se
# ejecuten dentro de FastAPI. Mismas dependencias de Python que services/api,
# porque la tarea ejecuta el pipeline de Prefect (Pandas, SQLModel) y habla
# con Supabase. Flower reutiliza esta misma imagen con otro comando.
#
# Contexto de build: la raíz del repo (ver docker-compose.yml). El archivo
# Dockerfile.dockerignore de al lado limita lo que se envía a Docker.
FROM python:3.12-slim

# libpq5: mismo motivo que en services/api/Dockerfile (psycopg2-binary).
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv
COPY services/api/requirements.txt /tmp/requirements.txt
RUN uv pip install --system --no-cache -r /tmp/requirements.txt

# El código llega por bind mount (docker-compose.yml): `services.celery_app`
# se importa desde /app, igual que desde la raíz del repo en dev nativo.
WORKDIR /app

CMD ["celery", "-A", "services.celery_app", "worker", "--loglevel=INFO", "--concurrency=1"]
