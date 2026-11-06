// SPDX-License-Identifier: Apache-2.0
import { slimRelease, type ReleaseJson } from "./assets.ts";

declare const __COPERCEIVE_RELEASE__: ReleaseJson | null;

const LATEST = "https://api.github.com/repos/adarsh-nl/coperceive/releases/latest";

/** The release baked in at build time, or null when that fetch did not succeed. */
export function embeddedRelease(): ReleaseJson | null {
  if (typeof __COPERCEIVE_RELEASE__ === "undefined") return null;
  return __COPERCEIVE_RELEASE__;
}

/** Build-time release when present, otherwise the same latest-release API from the browser. */
export async function loadRelease(): Promise<ReleaseJson | null> {
  const embedded = embeddedRelease();
  if (embedded && embedded.assets.length > 0) return embedded;
  try {
    const response = await fetch(LATEST, {
      headers: { Accept: "application/vnd.github+json" },
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) return null;
    return slimRelease(await response.json());
  } catch {
    return null;
  }
}
