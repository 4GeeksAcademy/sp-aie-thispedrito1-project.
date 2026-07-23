import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    const backendUrl =
      (globalThis as { process?: { env?: { BACKEND_API_URL?: string } } }).process?.env?.BACKEND_API_URL ||
      "http://127.0.0.1:8000";

    return [
      {
        source: "/api-proxy/:path*",
        destination: `${backendUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
