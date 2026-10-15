import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../pkg/app/static",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 8741,
    strictPort: true,
  },
});
