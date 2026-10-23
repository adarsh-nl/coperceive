import { copyFileSync, writeFileSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

function pagesExtras(): Plugin {
  return {
    name: "pages-extras",
    apply: "build",
    closeBundle() {
      copyFileSync("dist/index.html", "dist/404.html");
      writeFileSync("dist/.nojekyll", "");
    },
  };
}

export default defineConfig({
  base: "/coperceive/",
  plugins: [react(), pagesExtras()],
  server: { port: 5173, host: true },
  preview: { host: true, port: 4173 },
  build: {
    target: "es2022",
    sourcemap: false,
    modulePreload: {
      resolveDependencies(_filename, deps) {
        return deps.filter((dep) => !dep.includes("RealClip") && !dep.includes("three"));
      },
    },
  },
});
