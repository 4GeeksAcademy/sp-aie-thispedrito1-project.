#!/bin/sh
# Starts both Next.js dev servers from a single container: website on
# :3000, backoffice on :3001. -H 0.0.0.0 is required so each server accepts
# connections from outside the container, not just from localhost inside it.
# --webpack forces the classic bundler instead of Turbopack: Turbopack's
# incremental file-watching task graph panics ("inner_of_upper_lost_followers")
# over Docker's virtiofs-mounted bind mount on macOS — a Turbopack/virtiofs
# interaction, not something wrong with the app code. Native (non-Docker) dev
# is unaffected and keeps using Turbopack via each app's own `npm run dev`.
set -e

cleanup() {
  echo "Stopping website and backoffice..."
  kill -TERM "$WEBSITE_PID" "$BACKOFFICE_PID" 2>/dev/null
}
trap cleanup TERM INT

(cd /app/uis/website && npm run dev -- -p 3000 -H 0.0.0.0 --webpack) &
WEBSITE_PID=$!

(cd /app/uis/backoffice && npm run dev -- -p 3001 -H 0.0.0.0 --webpack) &
BACKOFFICE_PID=$!

wait "$WEBSITE_PID" "$BACKOFFICE_PID"
