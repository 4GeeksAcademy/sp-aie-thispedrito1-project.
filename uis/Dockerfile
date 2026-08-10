# Single dev image for both Next.js frontends (website + backoffice).
# Build context is /uis — website and backoffice get npm-installed
# separately, exactly as two independent apps, then both are started by
# start.sh on different ports. Source is bind-mounted at runtime for hot
# reload (see docker-compose.yml); the COPY steps below just make the image
# runnable standalone too (e.g. `docker run` without compose).
FROM node:20-alpine

# Next.js's native SWC compiler binary needs glibc; Alpine's musl libc
# doesn't provide it without this compatibility shim.
RUN apk add --no-cache libc6-compat

WORKDIR /app/uis

# Install dependencies first, keyed only on the lockfiles, so editing
# source code never invalidates this layer.
COPY website/package.json website/package-lock.json ./website/
COPY backoffice/package.json backoffice/package-lock.json ./backoffice/
RUN npm ci --prefix website && npm ci --prefix backoffice

COPY website ./website
COPY backoffice ./backoffice
COPY start.sh ./start.sh
RUN chmod +x start.sh

EXPOSE 3000 3001

# Invoked via `sh` rather than `./start.sh` on purpose: the docker-compose
# bind mount replaces this whole directory with the host's copy of it at
# runtime, and a file written on the host may not carry the execute bit
# Docker images normally get from a RUN chmod — `sh start.sh` works either way.
CMD ["sh", "start.sh"]
