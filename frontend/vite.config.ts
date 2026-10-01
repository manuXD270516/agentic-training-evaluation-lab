import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// El dashboard sólo lee la API: `/api/*` se reenvía al control plane sin prefijo. El destino
// se cambia con EVALLAB_API_URL (por defecto la API local de `uv run python -m evallab.api`).
const apiTarget = process.env["EVALLAB_API_URL"] ?? "http://127.0.0.1:8000";
const proxy = {
  "/api": {
    target: apiTarget,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ""),
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy,
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    strictPort: true,
    proxy,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
