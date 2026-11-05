// SPDX-License-Identifier: Apache-2.0
/**
 * Open Folder (docs/design/v0.1.1/App Pages.dc.html): the folder check shown
 * for the chosen folder and the Recent table. Everything shown comes from
 * `inspect_folder` (the folder's scene manifests); nothing is assumed.
 */
import { InspectorSection, usePageHeader, type GridColumn } from "./components/Page";
import type { FolderCheck } from "./local";

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

const KIND_LABEL: Record<FolderCheck["kind"], string> = {
  scene: "Scene",
  library: "Library",
  folder: "Folder",
  none: "—",
};

const MODALITY_LABEL: Record<string, string> = { camera: "Camera", lidar: "LiDAR" };

export function OpenFolderHeader({ active }: { active: boolean }) {
  usePageHeader("local", { meta: active ? "Any folder holding schema 1 scenes or datasets" : undefined });
  return null;
}

export function recentColumns(checks: Record<string, FolderCheck | null>): GridColumn<string>[] {
  const base = (root: string) => root.split("/").filter(Boolean).pop() || root;
  return [
    {
      key: "name",
      header: "Name",
      min: 140,
      max: 260,
      render: (root) => (
        <span style={{ color: "var(--text)", fontWeight: 500 }} aria-label={`Select recent dataset ${base(root)}`}>
          {base(root)}
        </span>
      ),
    },
    { key: "kind", header: "Kind", min: 80, max: 80, render: (root) => (checks[root] ? KIND_LABEL[checks[root].kind] : "—") },
    {
      key: "scenes",
      header: "Scenes",
      min: 60,
      max: 60,
      align: "right",
      render: (root) => {
        const check = checks[root];
        return check && check.kind !== "none" ? String(check.scene_ids.length) : "—";
      },
    },
    {
      key: "path",
      header: "Path",
      min: 200,
      max: 640,
      render: (root) => (
        <span className="c-mono" style={{ color: "var(--text-subtle)", fontSize: "12px" }}>
          {root}
        </span>
      ),
    },
  ];
}

export function FolderCheckPanel({
  check,
  busy,
  onOpen,
  onOpenFresh,
}: {
  /** undefined while the check runs; null when it cannot run (outside the app). */
  check: FolderCheck | null | undefined;
  busy: boolean;
  onOpen: () => void;
  onOpenFresh: () => void;
}) {
  // Opening is allowed while the check is still running; only a known problem stops it.
  const ok = check ? check.problem === null : true;
  const actions = (
    <InspectorSection>
      {/* The e2e and scripts open by this text. */}
      <button type="button" className="c-primary-wide" onClick={onOpen} disabled={busy || !ok} aria-label="Open Local Viewer">
        {busy ? "Connecting…" : "Open Local Viewer"}
      </button>
      <button
        type="button"
        className="c-secondary"
        onClick={onOpenFresh}
        disabled={busy || !ok}
        title="Connect without restoring saved session state"
        aria-label="Start fresh without restoring saved session state"
      >
        Open without restoring last session
      </button>
    </InspectorSection>
  );
  if (check === null) {
    return actions;
  }
  if (check === undefined) {
    return (
      <>
        <InspectorSection title="Folder check">
          <span style={{ color: "var(--text-subtle)" }}>Checking the folder…</span>
        </InspectorSection>
        {actions}
      </>
    );
  }
  const headline = !ok
    ? "Cannot open this folder"
    : check.kind === "scene"
      ? "Valid scene"
      : `Holds ${check.scene_ids.length} ${check.scene_ids.length === 1 ? "scene" : "scenes"}`;
  const rows: Array<[string, string, boolean]> = ok
    ? [
        ["Schema", check.kind === "scene" ? "schema 1 · scene manifest" : "schema 1 scenes", true],
        [
          "Scenes",
          check.scene_ids.length <= 3
            ? `${check.scene_ids.length} · ${check.scene_ids.join(", ")}`
            : `${check.scene_ids.length} · ${check.scene_ids.slice(0, 2).join(", ")}, …`,
          check.scene_ids.length > 0,
        ],
        [
          "Modalities",
          [...check.modalities.map((m) => MODALITY_LABEL[m] ?? m), ...(check.has_map ? ["map"] : [])].join(" · ") ||
            "none recorded",
          check.modalities.length > 0,
        ],
        ["Size", formatBytes(check.size_bytes), true],
      ]
    : [];
  return (
    <>
      <InspectorSection title="Folder check">
        <span className={`c-check-headline${ok ? " is-ok" : " is-bad"}`}>
          <span className="c-dot" />
          {headline}
        </span>
        {ok ? (
          <ul className="c-checklist">
            {rows.map(([label, value, good]) => (
              <li key={label}>
                <span aria-hidden="true">{good ? "✓" : "•"}</span>
                <span className="c-checklist-label">{label}</span>
                <span>{value}</span>
              </li>
            ))}
            <li>
              <span aria-hidden="true">•</span>
              <span className="c-checklist-label">Library</span>
              <span>{check.in_library ? "Already in your library" : "Not in your library"}</span>
            </li>
          </ul>
        ) : (
          <p style={{ margin: 0, color: "var(--danger)", fontSize: "12.5px" }}>{check.problem}</p>
        )}
      </InspectorSection>
      {actions}
    </>
  );
}
