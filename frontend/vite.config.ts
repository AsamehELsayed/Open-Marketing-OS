import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// DEV-004 W1: FastAPI stays the API. Dev proxy forwards API + chat/streaming
// to the local uvicorn server. Jinja routes remain untouched.
export default defineConfig({
  plugins: [
    react(),
    {
      name: "serve-start-here-assets-under-app-in-dev",
      configureServer(server) {
        // Production serves public screenshots beneath /app through the SPA
        // fallback. Mirror that path in Vite dev while keeping Vite's normal
        // publicDir URLs for every other asset.
        server.middlewares.use((req, _res, next) => {
          const request = req as unknown as { url?: string };
          if (request.url?.startsWith("/app/start-here/")) {
            request.url = request.url.replace(
              /^\/app\/start-here\//,
              "/start-here/",
            );
          }
          next();
        });
      },
    },
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/files": "http://localhost:8000",
      "/chat": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 800,
  },
});
