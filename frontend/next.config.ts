import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a minimal self-contained server bundle (.next/standalone) for
  // containerized deployment on Cloud Run.
  output: "standalone",
};

export default nextConfig;