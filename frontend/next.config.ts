import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Local-dev rewrite to the FastAPI backend on :8000.
  // When NEXT_PUBLIC_API_URL is set (Vercel / production), api.ts calls the
  // backend directly via that URL — no rewrite needed, CORS handles cross-origin.
  async rewrites() {
    if (process.env.NEXT_PUBLIC_API_URL) return [];
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
