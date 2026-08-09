import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Docker Desktop's virtiofs bind mount on macOS doesn't reliably forward
  // native filesystem-change events into the container, so webpack's
  // default watcher can miss host edits. Polling instead always works,
  // at a small CPU cost — only enabled inside the container (DOCKER_DEV),
  // never for native `npm run dev` on the host.
  ...(process.env.DOCKER_DEV ? { watchOptions: { pollIntervalMs: 1000 } } : {}),
};

export default nextConfig;
