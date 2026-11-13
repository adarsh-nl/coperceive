// SPDX-License-Identifier: Apache-2.0
/**
 * The Dataset view's cached scene indexes.
 *
 * Indexing a raw split is metadata only but on a cluster filesystem it lists
 * every frame of every scene: OPV2V train took 45–60 s to open every time. The
 * index is a pure function of the raw tree's metadata, so the last document is
 * kept per (server, adapter, raw root, split) and shown at once when the same
 * dataset is opened again; a background index then checks for changes
 * (`sha1-mtime-size-v1` fingerprint) and updates the view only if the raw tree
 * moved.
 *
 * The cache is the app window's own storage (the main window's origin is
 * stable, unlike a viewer server on a random port) with an in-memory map for
 * the current session. Nothing is written on the cluster: the raw tree and the
 * results cache stay read-only to this feature.
 */
import type { ClusterIndexDocument } from "./clusterDatasets.ts";

const STORAGE_KEY = "coperceive-cluster-index-cache";
/** Enough datasets for one workspace; each document is metadata only. */
const MAX_ENTRIES = 12;

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

function sameDataset(entry: StoredIndex, profileId: string, adapter: string, rawRoot: string, split: string | null): boolean {
  return (
    entry.profile_id === profileId &&
    entry.adapter === adapter &&
    entry.raw_root === rawRoot &&
    (entry.split ?? null) === (split ?? null)
  );
}

function readStore(): StoredIndex[] {
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? (parsed as StoredIndex[]) : [];
  } catch {
    return [];
  }
}

function writeStore(entries: StoredIndex[]): void {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Quota or a disabled store: the in-memory copy still serves this session.
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
  if (fromMemory) return { document: fromMemory.document, saved_unix: fromMemory.saved_unix };
  const found = readStore().find((entry) => sameDataset(entry, profileId, adapter, rawRoot, split));
  if (!found || isForeignDocument(found.document, adapter, rawRoot, split)) return null;
  memory.set(key, found);
  return { document: found.document, saved_unix: found.saved_unix };
}

/** Keep this dataset's index as the one to show at the next open. */
export function writeCachedIndex(
  profileId: string,
  adapter: string,
  rawRoot: string,
  split: string | null,
  document: ClusterIndexDocument,
  savedUnix: number = Math.floor(Date.now() / 1000),
): void {
  if (isForeignDocument(document, adapter, rawRoot, split)) return;
  const entry: StoredIndex = {
    profile_id: profileId,
    adapter,
    raw_root: rawRoot,
    split: split ?? null,
    saved_unix: savedUnix,
    document,
  };
  memory.set(keyOf(profileId, adapter, rawRoot, split), entry);
  const others = readStore().filter((candidate) => !sameDataset(candidate, profileId, adapter, rawRoot, split));
  const next = [...others, entry]
    .sort((left, right) => left.saved_unix - right.saved_unix)
    .slice(-MAX_ENTRIES);
  writeStore(next);
}

/** Forget one dataset's cache (a retry after an empty or failed index). */
export function forgetCachedIndex(
  profileId: string,
  adapter: string,
  rawRoot: string,
  split: string | null,
): void {
  memory.delete(keyOf(profileId, adapter, rawRoot, split));
  writeStore(
    readStore().filter((entry) => !sameDataset(entry, profileId, adapter, rawRoot, split)),
  );
}

function isForeignDocument(
  document: ClusterIndexDocument,
  adapter: string,
  rawRoot: string,
  split: string | null,
): boolean {
  if (document?.schema !== "coperceive-cluster-index-1") return true;
  if (document.adapter !== adapter) return true;
  if (document.raw_root !== rawRoot) return true;
  return (document.split ?? null) !== (split ?? null);
}

/**
 * Whether the background index found the same raw tree as the cached one.
 * The fingerprint is the index's own `sha1-mtime-size-v1` digest; a truncated
 * walk (or a different algorithm) is not trusted to be unchanged.
 */
export function indexIsUnchanged(
  cached: ClusterIndexDocument,
  fresh: ClusterIndexDocument,
): boolean {
  const left = cached?.fingerprint;
  const right = fresh?.fingerprint;
  if (!left || !right) return false;
  return (
    left.algorithm === right.algorithm &&
    left.digest === right.digest &&
    left.entries === right.entries &&
    left.truncated === false &&
    right.truncated === false
  );
}

/**
 * What to tell the user after the background index answered: null when nothing
 * changed, otherwise a short plain sentence. A changed digest updates the view.
 */
export function indexRefreshMessage(
  cached: ClusterIndexDocument,
  fresh: ClusterIndexDocument,
): string | null {
  if (indexIsUnchanged(cached, fresh)) return null;
  if (cached.scene_count === fresh.scene_count && fresh.scene_count > 0) {
    return "The raw files changed on the cluster; the scene list was refreshed.";
  }
  return `The scene list changed on the cluster: ${fresh.scene_count} scene${fresh.scene_count === 1 ? "" : "s"} now.`;
}

/**
 * Whether a fresh index should replace the one on screen. A cached scene list
 * is kept when the new index failed to read every scene (0 scenes with
 * per-scene errors): one bad walk must not blank a dataset the user was seeing.
 */
export function shouldReplaceIndex(
  cached: ClusterIndexDocument | null,
  fresh: ClusterIndexDocument,
): boolean {
  if (!cached) return true;
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
  try {
    if (typeof localStorage !== "undefined") localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing persisted to clear.
  }
}
