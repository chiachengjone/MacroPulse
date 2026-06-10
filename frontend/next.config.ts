import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // standalone output is only needed for Docker/Cloud Run.
  // Vercel handles bundling itself — setting this in Vercel builds is a no-op
  // but omitting it avoids any edge-case conflicts with Vercel's build system.
  ...(process.env.BUILD_STANDALONE === "true" && { output: "standalone" }),
};

export default nextConfig;