import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.indexOf("node_modules") === -1) return;
          if (id.indexOf("react-router") !== -1) return "router";
          if (id.indexOf("@tanstack") !== -1) return "query";
          if (id.indexOf("lucide-react") !== -1) return "icons";
          if (id.indexOf("zod") !== -1) return "validation";
          if (id.indexOf("react") !== -1 || id.indexOf("scheduler") !== -1) return "react-vendor";
        }
      }
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts"
  }
});
