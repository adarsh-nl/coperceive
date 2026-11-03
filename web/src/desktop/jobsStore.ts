// SPDX-License-Identifier: Apache-2.0
/**
 * Cluster datasets with prepare jobs in flight, for the always-visible jobs
 * indicator. Each open dataset view publishes its jobs and scene counts here;
 * the indicator lists them and opens a dataset on click. Kept in localStorage
 * so the list survives a restart (the dataset view refreshes it when opened).
 */

import type { ClusterDetectDocument } from "./clusterDatasets";

export interface DatasetJobs {
  /** `${profileId}|${datasetPath}` */
  key: string;
  profileId: string;
  label: string;
  jobIds: string[];
  ready: number;
  preparing: number;
  queued: number;
  total: number;
  viewerOpen: boolean;
  /** The dataset's detect document, to reopen it from the indicator. */
  dataset: ClusterDetectDocument | null;
  updatedAt: number;
}

type Listener = () => void;

const STORAGE_KEY = "coperceive-dataset-jobs";
const listeners = new Set<Listener>();
let entries: DatasetJobs[] = load();

function load(): DatasetJobs[] {
  try {
    const raw = typeof localStorage !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? (parsed as DatasetJobs[]) : [];
  } catch {
    return [];
  }
}

function save() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Not persisted; the in-memory list still works.
  }
}

export function datasetJobs(): DatasetJobs[] {
  return entries;
}

export function subscribeDatasetJobs(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** A dataset finished when none of its scenes is queued or preparing and no job runs. */
export function isFinished(entry: Pick<DatasetJobs, "jobIds" | "preparing" | "queued">): boolean {
  return entry.jobIds.length === 0 && entry.preparing === 0 && entry.queued === 0;
}

/**
 * Publish a dataset's state. Returns true when this update finished it (it had
 * work in flight and now has none), so the caller can tell the user once.
 */
export function publishDatasetJobs(next: Omit<DatasetJobs, "updatedAt">): boolean {
  const before = entries.find((entry) => entry.key === next.key);
  const finished = Boolean(before) && !isFinished(before!) && isFinished(next);
  const others = entries.filter((entry) => entry.key !== next.key);
  // A dataset with nothing in flight leaves the list (once it was announced).
  entries = isFinished(next) ? others : [...others, { ...next, updatedAt: Date.now() }];
  save();
  for (const listener of listeners) listener();
  return finished;
}

export function setViewerOpen(open: boolean, key?: string) {
  entries = entries.map((entry) => (key === undefined || entry.key === key ? { ...entry, viewerOpen: open } : entry));
  save();
  for (const listener of listeners) listener();
}

/** "15 ready · 3 preparing · 15 queued" */
export function jobsSummary(entry: Pick<DatasetJobs, "ready" | "preparing" | "queued">): string {
  return `${entry.ready} ready · ${entry.preparing} preparing · ${entry.queued} queued`;
}

/** Tests only. */
export function _resetDatasetJobs(): void {
  entries = [];
}
