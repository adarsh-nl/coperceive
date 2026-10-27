// SPDX-License-Identifier: Apache-2.0
/**
 * Pure helpers for browsing a remote server's data folders.
 *
 * The remote scan is metadata only and bounded; these helpers turn its JSON
 * into rows, breadcrumbs and plain-language explanations. They never invent a
 * path and never start work on the cluster: the Open action stays blocked until
 * the confirmed job-submission step exists.
 */
import type { FolderInfo, ScanDocument } from "./hpc";

/** Shown whenever the user tries to open a scanned scene: nothing starts yet. */
export const OPEN_BLOCKED_REASON =
  "Remote playback needs the confirmed job-submission step, which comes in the next slice. " +
  "Nothing was started on the cluster.";

export type BrowseRowKind = "scene" | "dataset" | "folder";

export interface BrowseRow {
  /** Path relative to the data root ("" is the root itself). */
  path: string;
  name: string;
  kind: BrowseRowKind;
  /** Plain-language detail for the badge line. */
  detail: string;
}

/**
 * Relative path segments that a scan may show, with empty and `..` segments
 * dropped so a hostile or broken reply can never walk above the data root.
 */
function safeParts(relativePath: string): string[] {
  const parts = relativePath.split("/");
  if (parts.some((part) => part === "..")) {
    // A scan never reports ".."; treat a hostile or broken reply as the root.
    return [];
  }
  return parts.filter((part) => part.length > 0 && part !== ".");
}

function nameFor(path: string): string {
  const parts = path.split("/").filter((part) => part.length > 0);
  return parts.length > 0 ? (parts[parts.length - 1] as string) : ".";
}

/** The scanned folder's path as a relative path ("" for the root). */
export function relativeBrowsePath(document: ScanDocument | null): string {
  if (!document) return "";
  const path = document.relative_path;
  return path === "." || path === "" ? "" : path;
}

/** The folder rows to show for a scan result: scenes first, then datasets, then folders. */
export function browseRows(document: ScanDocument | null): BrowseRow[] {
  if (!document) return [];
  const current = relativeBrowsePath(document);
  const rows: BrowseRow[] = [];
  for (const folder of document.folders) {
    if (folder.path === current || folder.path === ".") continue;
    const kind: BrowseRowKind = folder.is_scene
      ? "scene"
      : folder.dataset
        ? "dataset"
        : "folder";
    rows.push({
      path: folder.path,
      name: nameFor(folder.path),
      kind,
      detail: rowDetail(kind, folder),
    });
  }
  const order: Record<BrowseRowKind, number> = { scene: 0, dataset: 1, folder: 2 };
  return rows.sort((left, right) => {
    if (order[left.kind] !== order[right.kind]) return order[left.kind] - order[right.kind];
    return left.name.localeCompare(right.name);
  });
}

function rowDetail(kind: BrowseRowKind, folder: FolderInfo): string {
  if (kind === "scene") {
    return folder.scene_id ? `scene ${folder.scene_id}` : "scene";
  }
  if (kind === "dataset") {
    return `${folder.scene_count} scene${folder.scene_count === 1 ? "" : "s"}`;
  }
  const folders = `${folder.subfolder_count} folder${folder.subfolder_count === 1 ? "" : "s"}`;
  const files = `${folder.file_count} file${folder.file_count === 1 ? "" : "s"}`;
  return `${folders} · ${files}`;
}

/** Breadcrumbs from the data root to the current folder; the last one is current. */
export function browseCrumbs(
  root: string,
  relativePath: string,
): { label: string; path: string }[] {
  const crumbs = [{ label: root, path: "" }];
  const parts = safeParts(relativePath);
  let current = "";
  for (const part of parts) {
    current = current === "" ? part : `${current}/${part}`;
    crumbs.push({ label: part, path: current });
  }
  return crumbs;
}

/** The parent folder's relative path ("" at the root). */
export function parentBrowsePath(relativePath: string): string {
  const parts = safeParts(relativePath);
  parts.pop();
  return parts.join("/");
}

/** The child folder's relative path, for a folder row. */
export function childBrowsePath(relativePath: string, child: string): string {
  const parts = safeParts(child);
  const safeChild = parts.join("/");
  if (!safeChild) return safeParts(relativePath).join("/");
  const parent = safeParts(relativePath).join("/");
  return parent ? `${parent}/${safeChild}` : safeChild;
}

/** Plain-language note when a bound stopped the scan, or null. */
export function truncatedMessage(document: ScanDocument | null): string | null {
  if (!document || !document.truncated) return null;
  const reason = (document.truncated_reason ?? "").toLowerCase();
  if (reason.includes("time")) {
    return (
      `The scan stopped after ${document.timeout_s} s so the cluster's login node stays free. ` +
      "Showing what it read so far."
    );
  }
  if (reason.includes("entry")) {
    return (
      `This folder has more entries than one scan reads at once (${document.max_entries} per ` +
      "folder). Showing what it read so far."
    );
  }
  if (reason.includes("total")) {
    return (
      `This tree has more folders than one scan describes at once (${document.max_total}). ` +
      "Showing what it read so far."
    );
  }
  return `The scan stopped early${document.truncated_reason ? ` (${document.truncated_reason})` : ""}. Showing what it read so far.`;
}

/** One plain sentence for each entry the scan did not follow. */
export function skippedMessages(document: ScanDocument | null): string[] {
  if (!document) return [];
  return document.skipped.map((entry) => `${entry.path}: ${entry.reason}.`);
}

/** One-line summary of what a scan found, including that it was metadata only. */
export function scanSummary(document: ScanDocument | null): string {
  if (!document) return "No folder scanned yet.";
  const parts = [
    `${document.folder_count} folder${document.folder_count === 1 ? "" : "s"}`,
    `${document.scene_count} scene${document.scene_count === 1 ? "" : "s"}`,
    `${document.dataset_count} dataset${document.dataset_count === 1 ? "" : "s"}`,
  ];
  return `${parts.join(" · ")} read in ${Math.max(0, Math.round(document.elapsed_ms))} ms.`;
}

/** What a selected row is, for the selection line and the blocked Open button. */
export function selectionLabel(document: ScanDocument | null, path: string | null): string | null {
  if (!document || path === null || path === "") return null;
  const row = browseRows(document).find((candidate) => candidate.path === path);
  if (!row) return null;
  if (row.kind === "scene") return `Scene ${row.name}`;
  if (row.kind === "dataset") return `Dataset ${row.name} (${row.detail})`;
  return `Folder ${row.name}`;
}

/** True when the selected path is a scene folder; Open is still blocked in this slice. */
export function selectionIsScene(document: ScanDocument | null, path: string | null): boolean {
  if (!document || path === null) return false;
  return browseRows(document).some((row) => row.path === path && row.kind === "scene");
}
