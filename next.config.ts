import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `NEXT_OUTPUT=export` builds a static site, served by FastAPI in the live demo.
  ...(process.env.NEXT_OUTPUT === "export" ? { output: "export" as const } : {}),
};

export default nextConfig;
