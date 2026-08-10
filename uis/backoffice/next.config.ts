// uis/backoffice/next.config.ts
import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  experimental: {
    externalDir: true,
  },
  turbopack: {
    // sube a la raíz real del monorepo, no a esta carpeta —
    // externalDir exige que Turbopack pueda ver y resolver módulos fuera de backoffice
    root: path.join(__dirname, "../.."),
  },
  allowedDevOrigins: ["192.168.68.102"], // permite HMR desde tu IP de red local, visto en el warning del log
  // Docker Desktop's virtiofs bind mount on macOS doesn't reliably forward
  // native filesystem-change events into the container, so webpack's
  // default watcher can miss host edits. Polling instead always works, at
  // a small CPU cost — only enabled inside the container (DOCKER_DEV).
  ...(process.env.DOCKER_DEV ? { watchOptions: { pollIntervalMs: 1000 } } : {}),
  async rewrites() {
    const backendUrl = process.env.BACKEND_API_URL || "http://127.0.0.1:8000";
    return [
      {
        source: "/api-proxy/:path*",
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;