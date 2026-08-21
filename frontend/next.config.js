/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Fully static build — served by the FastAPI backend (one service, one port).
  output: "export",
  // next/image requires a server; we use plain <img>, but keep this explicit.
  images: { unoptimized: true },
};

module.exports = nextConfig;
