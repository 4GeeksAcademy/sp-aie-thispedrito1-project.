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

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
