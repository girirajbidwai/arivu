/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: {
    DASHBOARD_API_URL: process.env.DASHBOARD_API_URL || "http://localhost:8001",
    NEXT_PUBLIC_DASHBOARD_API_URL:
      process.env.NEXT_PUBLIC_DASHBOARD_API_URL ||
      process.env.DASHBOARD_API_URL ||
      "http://localhost:8001",
    NEXT_PUBLIC_VOICE_SERVICE_URL:
      process.env.NEXT_PUBLIC_VOICE_SERVICE_URL ||
      process.env.VOICE_SERVICE_URL ||
      "http://localhost:8000",
  },
};

module.exports = nextConfig;
