# Dev image for the FastAPI backend. Uses python:3.12-slim rather than an
# Alpine base: psycopg2-binary (needed for the Supabase/PostgreSQL
# inventory module) ships manylinux (glibc) wheels, not musl ones, so
# Alpine would force a from-source compile. Packages install into the
# image's global site-packages (never into a .venv under this directory)
# so the docker-compose bind mount of services/api/ can't shadow them.
FROM python:3.12-slim

WORKDIR /app/services/api

# libpq5: psycopg2-binary's wheel usually bundles what it needs, but slim
# base images occasionally lack a system libpq that some wheel builds still
# dynamically link against — cheap insurance against a runtime ImportError.
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY requirements.txt ./
RUN uv pip install --system --no-cache -r requirements.txt

COPY . .

# Usuario sin privilegios (auditoría OWASP A05: el agente, el RAG y el
# cliente MCP corren dentro de este proceso). Las dependencias ya quedaron
# instaladas como root, así que la app no puede modificarlas. UID 1000 = el
# dueño habitual de los bind mounts en Linux (Codespaces, servidor), para que
# la TinyDB y data/ sigan siendo escribibles. Sin montajes, solo data/.
ARG APP_UID=1000
RUN useradd --uid "${APP_UID}" --create-home --shell /usr/sbin/nologin healthcore \
    && mkdir -p /app/services/api/data \
    && chown -R healthcore:healthcore /app/services/api/data
USER healthcore

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
