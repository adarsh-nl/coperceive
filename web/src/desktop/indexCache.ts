// SPDX-License-Identifier: Apache-2.0
/**
 * The Dataset view's cached scene indexes.
 *
 * Indexing a raw split is metadata only but on a cluster filesystem it lists
 * every frame of every scene: OPV2V train took 45–60 s on the login node. The
 * index is a pure function of the raw tree's metadata, so the last document is
 * kept per (server, adapter, raw root, split) and shown at once when the same
 * dataset is opened again, labelled cached; the same read-only index command
 * then re-checks the tree in the background (`sha1-mtime-size-v1` fingerprint)
 * and updates the view only when the raw files really differ.
 *
 * The cache is the app window's own storage (the main window's origin is
 * stable, unlike a viewer server on a random port) with an in-memory map for
 * the current session. Nothing is written on the cluster: the raw tree and the
 * results cache stay read-only to this feature. A document read back from
 * storage is validated first: one truncated or malformed entry is dropped, not
 * handed to a component.
 */
import type { ClusterIndexDocument } from "./clusterDatasets.ts";

const STORAGE_KEY = "coperceive-cluster-index-cache";
/** Keep the cache small: it holds metadata documents only, never scene data. */
const MAX_BYTES = 8 * 1024 * 1024;
let maxBytes = MAX_BYTES;

export interface CachedIndex {
  document: ClusterIndexDocument;
  /** Unix seconds when this document was indexed. */
  saved_unix: number;
}

interface StoredIndex extends CachedIndex {
  profile_id: string;
  adapter: string;
  raw_root: string;
  split: string | null;
}

/** Presentation order: the in-memory copy wins, then localStorage, per key. */
const memory = new Map<string, StoredIndex>();

function keyOf(
  profileId: string,
  adapter: string,
  rawRoot: string,
  split: string | null,
): string {
  return JSON.stringify([profileId, adapter, rawRoot, split]);
}

function sameDataset(
  entry: StoredIndex,
  profileId: string,
  adapter: string,
  rawRoot: string,
  split: string | null,
): boolean {
  return (
    entry.profile_id === profileId &&
    entry.adapter === adapter &&
    entry.raw_root === rawRoot &&
    (entry.split ?? null) === (split ?? null)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function validScene(value: unknown): boolean {
  if (!isRecord(value)) return false;
  if (typeof value.scene_id !== "string" || value.scene_id.length === 0) return false;
  if (!isNumber(value.sample_count) || !isNumber(value.agent_count)) return false;
  if (value.duration_s !== null && !isNumber(value.duration_s)) return false;
  const sensors = value.sensors;
  if (!isRecord(sensors)) return false;
  return isNumber(sensors.sensors) && isNumber(sensors.camera_samples) && isNumber(sensors.lidar_samples);
}

/**
 * Shape check for a document read back from storage. A cached document is
 * untrusted input: an outdated or damaged entry must be dropped, never passed
 * to the view where a missing `scenes` array would crash it.
 */
export function validIndexDocument(value: unknown): value is ClusterIndexDocument {
  if (!isRecord(value)) return false;
  if (value.schema !== "coperceive-cluster-index-1") return false;
  if (typeof value.adapter !== "string" || value.adapter.length === 0) return false;
  if (typeof value.raw_root !== "string" || value.raw_root.length === 0) return false;
  if (value.split !== null && typeof value.split !== "string") return false;
  if (!isNumber(value.scene_count) || value.scene_count < 0) return false;
  if (!Array.isArray(value.scenes) || !value.scenes.every(validScene)) return false;
  if (!Array.isArray(value.errors) || !value.errors.every((line) => typeof line === "string")) {
    return false;
  }
  const fingerprint = value.fingerprint;
  if (
    !isRecord(fingerprint) ||
    typeof fingerprint.algorithm !== "string" ||
    typeof fingerprint.digest !== "string" ||
    !isNumber(fingerprint.entries) ||
    typeof fingerprint.truncated !== "boolean"
  ) {
    return false;
  }
  const cache = value.cache;
  if (!isRecord(cache) || typeof cache.relative_path !== "string") return false;
  return true;
}

function validEntry(value: unknown): value is StoredIndex {
  if (!isRecord(value)) return false;
  if (typeof value.profile_id !== "string" || value.profile_id.length === 0) return false;
  if (typeof value.adapter !== "string" || typeof value.raw_root !== "string") return false;
  if (value.split !== null && typeof value.split !== "string") return false;
  if (!isNumber(value.saved_unix)) return false;
  return validIndexDocument(value.document);
}

function storageOrNull(): Storage | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

function readStore(): StoredIndex[] {
  const storage = storageOrNull();
  if (!storage) return [];
  let raw: string | null;
  try {
    raw = storage.getItem(STORAGE_KEY);
  } catch {
    return [];
  }
  if (!raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    try {
      storage.removeItem(STORAGE_KEY);
    } catch {
      // Nothing to clean up.
    }
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const entries = parsed.filter(validEntry);
  if (entries.length !== parsed.length) {
    // Drop the damaged entries the next write refreshes anyway.
    writeStore(entries);
  }
  return entries;
}

function encodedBytes(entries: StoredIndex[]): number {
  try {
    return new TextEncoder().encode(JSON.stringify(entries)).length;
  } catch {
    return Number.POSITIVE_INFINITY;
  }
}

function oldestFirst(entries: StoredIndex[]): StoredIndex[] {
  return [...entries].sort((left, right) => left.saved_unix - right.saved_unix);
}

function trimToBytes(entries: StoredIndex[]): StoredIndex[] {
  const trimmed = oldestFirst(entries);
  while (trimmed.length > 0 && encodedBytes(trimmed) > maxBytes) {
    trimmed.shift();
  }
  return trimmed;
}

/**
 * Write the store, evicting the oldest entries when it is over the byte cap or
 * the browser reports a full quota. Returns the entries that were persisted,
 * or null when even an empty store cannot be written (and says so on the
 * console rather than failing silently; the in-memory copy still serves this
 * session).
 */
function writeStore(entries: StoredIndex[]): StoredIndex[] | null {
  const storage = storageOrNull();
  if (!storage) return null;
  const candidates = trimToBytes(entries);
  for (;;) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(candidates));
      return candidates;
    } catch {
      if (candidates.length === 0) break;
      candidates.shift();
    }
  }
  try {
    storage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing else can be done; the memory copy is still valid.
  }
  console.warn(
    "coperceive: the scene-index cache could not be saved to this window's storage; it is kept in memory for this session.",
  );
  return null;
}

/** Make the session copy hold exactly what storage holds (after eviction). */
function syncMemory(kept: StoredIndex[]): void {
  const keys = new Set(
    kept.map((entry) => keyOf(entry.profile_id, entry.adapter, entry.raw_root, entry.split)),
  );
  for (const key of [...memory.keys()]) {
    if (!keys.has(key)) memory.delete(key);
  }
  for (const entry of kept) {
    memory.set(keyOf(entry.profile_id, entry.adapter, entry.raw_root, entry.split), entry);
  }
}

/** The cached index for exactly this dataset, or null. Never runs a command. */
export function readCachedIndex(
  profileId: string,
  adapter: string,
  rawRoot: string,
  split: string | null,
): CachedIndex | null {
  const key = keyOf(profileId, adapter, rawRoot, split);
  const fromMemory = memory.get(key);
  if (fromMemory) {
    return { document: fromMemory.document, saved_unix: fromMemory.saved_unix };
  }
  const store = readStore();
  const found = store.find((entry) => sameDataset(entry, profileId, adapter, rawRoot, split));
  if (!found || !validIndexDocument(found.document)) {
    if (found) {
      memory.delete(key);
      writeStore(store.filter((entry) => entry !== found));
    }
    return null;
  }
  memory.set(key, found);
  return { document: found.document, saved_unix: found.saved_unix };
}

/**
 * Keep this dataset's index as the one to show at the next open. Returns
 * whether it reached storage (false when the document is malformed or the
 * storage is full even after evicting everything older).
 */
export function writeCachedIndex(
  profileId: string,
  adapter: string,
  rawRoot: string,
  split: string | null,
  document: ClusterIndexDocument,
  savedUnix: number = Math.floor(Date.now() / 1000),
): boolean {
  if (!validIndexDocument(document)) return false;
  if (document.adapter !== adapter || document.raw_root !== rawRoot) return false;
  const entry: StoredIndex = {
    profile_id: profileId,
    adapter,
    raw_root: rawRoot,
    split: split ?? null,
    saved_unix: savedUnix,
    document,
  };
  const others = readStore().filter(
    (candidate) => !sameDataset(candidate, profileId, adapter, rawRoot, split),
  );
  const kept = writeStore([...others, entry]);
  if (kept) syncMemory(kept);
  else memory.set(keyOf(profileId, adapter, rawRoot, split), entry);
  return kept !== null;
}

/** Forget one dataset's cache (a retry after an empty or failed index). */
export function forgetCachedIndex(
  profileId: string,
  adapter: string,
  rawRoot: string,
  split: string | null,
): void {
  memory.delete(keyOf(profileId, adapter, rawRoot, split));
  const kept = writeStore(
    readStore().filter((entry) => !sameDataset(entry, profileId, adapter, rawRoot, split)),
  );
  if (kept) syncMemory(kept);
}

/**
 * Drop cached indexes of server profiles that no longer exist, so removing a
 * server removes its documents too. Returns how many entries were dropped.
 */
export function pruneIndexCache(profileIds: readonly string[]): number {
  const allowed = new Set(profileIds);
  const store = readStore();
  const kept = store.filter((entry) => allowed.has(entry.profile_id));
  const dropped = store.length - kept.length;
  let memoryOnly = 0;
  for (const [key, entry] of memory) {
    if (allowed.has(entry.profile_id)) continue;
    memory.delete(key);
    const persisted = store.some((candidate) =>
      sameDataset(candidate, entry.profile_id, entry.adapter, entry.raw_root, entry.split),
    );
    if (!persisted) memoryOnly += 1;
  }
  if (kept.length !== store.length) {
    const persisted = writeStore(kept);
    if (persisted) syncMemory(persisted);
  }
  return dropped + memoryOnly;
}

/** Bytes the persisted cache currently uses (tests and diagnostics). */
export function indexCacheBytes(): number {
  return encodedBytes(readStore());
}

/** Tests only: how many entries the persisted cache holds. */
export function indexCacheEntryCount(): number {
  return readStore().length;
}

export type IndexChange = "unchanged" | "changed" | "uncertain";

function sceneSignature(document: ClusterIndexDocument): string {
  const ids = (document.scenes ?? []).map((scene) => scene.scene_id).join("\n");
  return `${document.scene_count}\n${ids}`;
}

/**
 * What the background index says about the cached one.
 *
 * Only a real difference in the metadata digest (with both walks complete) is
 * "changed". A walk that hit its time/entry cap cannot prove the tree is
 * unchanged, but on a real OPV2V tree the 200,000-entry/30 s bound is common,
 * so it must not make every open claim the raw files changed either. A
 * different scene list (ids or count) is a real difference even then; an
 * otherwise identical capped walk is "uncertain" and changes nothing.
 */
export function indexChange(
  cached: ClusterIndexDocument,
  fresh: ClusterIndexDocument,
): IndexChange {
  const left = cached?.fingerprint;
  const right = fresh?.fingerprint;
  if (!left || !right || left.algorithm !== right.algorithm) return "changed";
  const capped = Boolean(left.truncated || right.truncated);
  if (capped) {
    return sceneSignature(cached) === sceneSignature(fresh) ? "uncertain" : "changed";
  }
  return left.digest === right.digest ? "unchanged" : "changed";
}

/**
 * What to tell the user after the background index answered: null when the
 * cached list is confirmed current **or** when a capped walk simply could not
 * confirm it (saying so on every open of a large dataset would be noise). A
 * real change gets a plain sentence.
 */
export function indexRefreshMessage(
  cached: ClusterIndexDocument,
  fresh: ClusterIndexDocument,
): string | null {
  if (indexChange(cached, fresh) !== "changed") return null;
  if (cached.scene_count === fresh.scene_count && fresh.scene_count > 0) {
    return "The raw files changed on the cluster; the scene list was refreshed.";
  }
  return `The scene list changed on the cluster: ${fresh.scene_count} scene${fresh.scene_count === 1 ? "" : "s"} now.`;
}

/** Whether a fresh index should replace the one on screen. */
export function shouldReplaceIndex(
  cached: ClusterIndexDocument | null,
  fresh: ClusterIndexDocument,
): boolean {
  if (!cached) return true;
  // One bad walk must not blank a dataset the user was seeing.
  if (fresh.scene_count === 0 && (fresh.errors ?? []).length > 0 && cached.scene_count > 0) {
    return false;
  }
  return true;
}

/** A zero-scene document with read errors is not worth remembering. */
export function shouldCacheIndex(document: ClusterIndexDocument): boolean {
  return document.scene_count > 0 || (document.errors ?? []).length === 0;
}

/** Tests only: forget every cached index, in memory and in storage. */
export function _resetIndexCache(): void {
  memory.clear();
  maxBytes = MAX_BYTES;
  try {
    storageOrNull()?.removeItem(STORAGE_KEY);
  } catch {
    // Nothing persisted to clear.
  }
}

/** Tests only: shrink the byte cap so eviction can be asserted cheaply. */
export function _setMaxCacheBytesForTesting(bytes: number): void {
  maxBytes = bytes;
}
