import { copyFileSync, writeFileSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { slimRelease } from "./src/download/assets.ts";

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

const LATEST = "https://api.github.com/repos/adarsh-nl/coperceive/releases/latest";

/** Build-time release. A failed fetch leaves null and the page asks GitHub itself. */
async function embeddedRelease(): Promise<string> {
  try {
    const response = await fetch(LATEST, {
      headers: { Accept: "application/vnd.github+json", "User-Agent": "coperceive-site" },
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) return "null";
    return JSON.stringify(slimRelease(await response.json()));
  } catch {
    return "null";
  }
}

export default defineConfig(async () => ({
  base: "/coperceive/",
  define: { __COPERCEIVE_RELEASE__: await embeddedRelease() },
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
}));
