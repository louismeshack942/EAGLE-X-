/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Standalone output is required by the Render Docker image (runs server.js).
  output: "standalone",
};

module.exports = nextConfig;
