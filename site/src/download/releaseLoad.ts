// SPDX-License-Identifier: Apache-2.0
import { slimRelease, type ReleaseJson } from "./assets.ts";

declare const __COPERCEIVE_RELEASE__: ReleaseJson | null;

const LATEST = "https://api.github.com/repos/adarsh-nl/coperceive/releases/latest";
const STORAGE_KEY = "coperceive-latest-release";

export type ReleaseFetch = (url: string, init?: RequestInit) => Promise<Response>;

export interface ReleaseStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface ReleaseSource {
  fetch?: ReleaseFetch;
  storage?: ReleaseStore | null;
}

/** The release baked in at build time, or null when that fetch did not succeed. */
export function embeddedRelease(): ReleaseJson | null {
  if (typeof __COPERCEIVE_RELEASE__ === "undefined") return null;
  return __COPERCEIVE_RELEASE__;
}

function browserStore(): ReleaseStore | null {
  try {
    if (typeof sessionStorage === "undefined") return null;
    return sessionStorage;
  } catch {
    return null;
  }
}

export function readCachedRelease(storage: ReleaseStore | null | undefined): ReleaseJson | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return slimRelease(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function writeCachedRelease(storage: ReleaseStore | null | undefined, release: ReleaseJson): void {
  if (!storage) return;
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(release));
  } catch {
    // Private mode and a full quota both throw. The live value is still shown.
  }
}

/**
 * A live release replaces the embed when its tag differs. A failed or empty
 * live result leaves the embed in place.
 */
export function shownRelease(embedded: ReleaseJson | null, live: ReleaseJson | null): ReleaseJson | null {
  if (!live || live.assets.length === 0) return embedded;
  if (!embedded || (live.tag_name && live.tag_name !== embedded.tag_name)) return live;
  return embedded;
}

/**
 * The embed is what the page can paint immediately. This still asks GitHub,
 * including when the embed already has assets, so a release published after
 * the site was built takes over. One successful answer is kept in
 * sessionStorage for the rest of the tab, because the unauthenticated API
 * allows 60 requests an hour per IP. A failure or a 403 keeps the embed,
 * or the cached live release when this tab already fetched one.
 */
export async function refreshRelease(
  embedded: ReleaseJson | null,
  source: ReleaseSource = {},
): Promise<ReleaseJson | null> {
  const storage = source.storage === undefined ? browserStore() : source.storage;
  const cached = readCachedRelease(storage);
  if (cached) return shownRelease(embedded, cached);

  const fetchRelease = source.fetch ?? fetch;
  try {
    const response = await fetchRelease(LATEST, {
      headers: { Accept: "application/vnd.github+json" },
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) return embedded;
    const live = slimRelease(await response.json());
    if (live) writeCachedRelease(storage, live);
    return shownRelease(embedded, live);
  } catch {
    return embedded;
  }
}

export async function loadRelease(): Promise<ReleaseJson | null> {
  return refreshRelease(embeddedRelease());
}
