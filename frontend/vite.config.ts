import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Same address as production, so the SameSite=Strict login cookie works in development.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    include: ["src/**/*.test.ts"],
  },
});
