// SPDX-License-Identifier: Apache-2.0
/**
 * Browse a remote server's data folders (docs/design/v0.1.1/App Pages.dc.html,
 * Servers): a breadcrumb path, the folders listed under it, and an inspector
 * for the selected folder.
 *
 * Metadata only: one `coperceive remote scan` over the reused SSH connection
 * per folder lists the subfolders, their file counts and what each one is
 * ("Recognised as", from the same listing). Opening a dataset runs the usual
 * `dataset detect` on the selected folder first; nothing starts on the cluster.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type React from "react";
import { browseCrumbs, detectionIsCurrent, folderCheckState, relativeBrowsePath, skippedMessages, truncatedMessage } from "./browse";
import type { FolderInfo, ScanDocument, ServerEntry } from "./hpc";
import { remoteScan } from "./local";
import { datasetDetect, formatLicenceInfo, type ClusterDetectDocument } from "./clusterDatasets";
import { errorText } from "./errors";
import { GridTable, InspectorSection, PageLayout, PageToolbar, type GridColumn } from "./components/Page";

function nameOf(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function isChild(current: string, path: string): boolean {
  if (path === "." || path === current) return false;
  const prefix = current ? `${current}/` : "";
  return path.startsWith(prefix) && !path.slice(prefix.length).includes("/");
}

/** The label shown in "Recognised as": what the scan read, else nothing. */
function recognisedOf(folder: FolderInfo): string | null {
  return folder.recognised ?? null;
}

export default function HpcBrowse({
  profile,
  header,
  onOpenDataset,
}: {
  profile: ServerEntry;
  /** The server's own row (name, host, Test / Edit), shown above the path. */
  header?: React.ReactNode;
  onOpenDataset?: (detected: ClusterDetectDocument) => void;
}) {
  const roots = profile.profile.data_roots.filter((root) => root.trim().length > 0);
  const firstRoot = roots[0] ?? "";
  const [root, setRoot] = useState<string>(firstRoot);
  const [document, setDocument] = useState<ScanDocument | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [detected, setDetected] = useState<ClusterDetectDocument | null>(null);
  // The folder path `detected` was read for: a detection that finished for a
  // folder the user has moved off must never open the wrong dataset.
  const [detectedFor, setDetectedFor] = useState<string | null>(null);
  // The folder path whose check finished, whatever it found: an answered
  // "not a dataset" must replace "Checking what this folder holds…".
  const [checkedFor, setCheckedFor] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [detectError, setDetectError] = useState("");

  // Set once a test seed arrives: the seeded listing then stands.
  const seeded = useRef(false);

  const load = async (nextRoot: string, nextPath: string) => {
    if (seeded.current) return;
    setLoading(true);
    setError("");
    try {
      const result = await remoteScan(profile.id, nextRoot, nextPath);
      setRoot(nextRoot);
      setDocument(result);
      setSelected(null);
    } catch (e) {
      setError(errorText(e));
      setDocument(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setRoot(firstRoot);
    setDocument(null);
    setError("");
    setSelected(null);
    if (firstRoot) void load(firstRoot, "");
    // Rescan only when another server is shown.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.id]);

  // Test-only: a seeded scan, as the screenshot tests have no cluster.
  useEffect(() => {
    const handleSeed = (event: Event) => {
      const custom = event as CustomEvent<{ document?: ScanDocument; root?: string }>;
      if (custom.detail?.document) {
        seeded.current = true;
        setRoot(custom.detail.root ?? custom.detail.document.root);
        setDocument(custom.detail.document);
        setError("");
        setLoading(false);
      }
    };
    window.addEventListener("coperceive-test-hpc-scan-seed", handleSeed);
    return () => window.removeEventListener("coperceive-test-hpc-scan-seed", handleSeed);
  }, []);

  const current = relativeBrowsePath(document);
  const crumbs = browseCrumbs(root, current);
  const rows = useMemo(
    () =>
      (document?.folders ?? [])
        .filter((folder) => isChild(current, folder.path))
        .sort((left, right) => nameOf(left.path).localeCompare(nameOf(right.path))),
    [document, current],
  );
  // The first folder is selected by default.
  useEffect(() => {
    if (rows.length && (selected === null || !rows.some((row) => row.path === selected))) {
      setSelected(rows[0].path);
    }
  }, [rows, selected]);
  const folder = rows.find((row) => row.path === selected) ?? null;
  const fullPath = (relative: string) =>
    relative ? `${root.replace(/\/+$/, "")}/${relative.replace(/^\/+/, "")}` : root;

  // What "Open dataset" would open: the usual detect, on the selected folder.
  useEffect(() => {
    setDetected(null);
    setDetectedFor(null);
    setCheckedFor(null);
    setDetectError("");
    if (!folder || !recognisedOf(folder) || recognisedOf(folder)?.startsWith("Checkpoints")) {
      setDetecting(false);
      return;
    }
    const target = fullPath(folder.path);
    let active = true;
    setDetecting(true);
    const timer = window.setTimeout(() => {
      datasetDetect(profile.id, target)
        .then((result) => {
          if (!active) return;
          setCheckedFor(target);
          if (result.status === "ok") {
            setDetected(result);
            setDetectedFor(target);
          } else if (result.status === "error") {
            setDetectError(result.reason || "The cluster refused the detection.");
          }
        })
        .catch((e: unknown) => {
          if (active) {
            setCheckedFor(target);
            setDetectError(errorText(e));
          }
        })
        .finally(() => {
          if (active) setDetecting(false);
        });
    }, 150);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
    // fullPath depends on root, which the folder path is relative to.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.id, folder?.path, root]);

  const selectedPath = folder ? fullPath(folder.path) : null;
  // Only the selected folder's own completed check may open its dataset: not
  // another folder's leftover result, and not a check still running.
  const detectedIsCurrent = detected !== null && detectionIsCurrent(detectedFor, selectedPath);
  const checkState = folderCheckState({
    detectedIsCurrent,
    detecting,
    detectError,
    checkedFor,
    selectedPath,
  });

  const openDataset = () => {
    if (!detected || !detectedIsCurrent) return;
    if (onOpenDataset) onOpenDataset(detected);
    else window.dispatchEvent(new CustomEvent("coperceive-open-dataset", { detail: { profile, dataset: detected } }));
  };

  const columns: GridColumn<FolderInfo>[] = [
    {
      key: "folder",
      header: "Folder",
      min: 180,
      max: 360,
      render: (row) => (
        <span className="c-mono" style={{ color: "var(--text)" }}>
          {nameOf(row.path)}
        </span>
      ),
    },
    { key: "folders", header: "Folders", min: 64, max: 64, align: "right", render: (row) => row.subfolder_count },
    { key: "files", header: "Files", min: 52, max: 52, align: "right", render: (row) => row.file_count },
    {
      key: "recognised",
      header: "Recognised as",
      min: 160,
      max: 320,
      render: (row) => {
        const label = recognisedOf(row);
        return label ? (
          <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span className="c-dot" style={{ width: "6px", height: "6px", background: "#8e8e93" }} />
            {label}
          </span>
        ) : (
          <span style={{ color: "var(--text-subtle)" }}>—</span>
        );
      },
    },
    {
      key: "go",
      header: "",
      min: 24,
      max: 24,
      align: "right",
      render: (row) => (
        <button
          type="button"
          className="c-link"
          aria-label={`Open folder ${nameOf(row.path)}`}
          onClick={(event) => {
            event.stopPropagation();
            void load(root, row.path);
          }}
        >
          ›
        </button>
      ),
    },
  ];

  const note = truncatedMessage(document);
  const skipped = skippedMessages(document);

  return (
    <PageLayout
      inspectorLabel="Selected folder"
      inspector={
        folder ? (
          <>
            <InspectorSection title="Selected folder">
              <div className="c-mono" style={{ fontSize: "14px", color: "var(--text)" }}>
                {nameOf(folder.path)}
              </div>
              <div className="c-mono" style={{ fontSize: "11.5px", color: "var(--text-subtle)", overflowWrap: "anywhere" }}>
                {fullPath(folder.path)}
              </div>
            </InspectorSection>
            <InspectorSection>
              <dl className="c-facts">
                <div className="c-fact">
                  <dt>Folders</dt>
                  <dd>{folder.subfolder_count}</dd>
                </div>
                <div className="c-fact">
                  <dt>Files</dt>
                  <dd>{folder.file_count}</dd>
                </div>
              </dl>
              {recognisedOf(folder) ? (
                <div>
                  <div className="c-inspector-label">Recognised as</div>
                  <div style={{ color: detected ? "var(--ok)" : "var(--text)", marginTop: "2px" }}>{recognisedOf(folder)}</div>
                </div>
              ) : null}
              {checkState === "detected" && detected ? (
                <span style={{ fontSize: "12px" }}>
                  {[
                    detected.split ? `split ${detected.split}` : null,
                    `${detected.scene_count} scene${detected.scene_count === 1 ? "" : "s"}`,
                    detected.licence ? formatLicenceInfo(detected.licence).statusText : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              ) : checkState === "checking" ? (
                <span style={{ fontSize: "12px", color: "var(--text-subtle)" }}>Checking what this folder holds…</span>
              ) : checkState === "error" ? (
                <span style={{ fontSize: "12px", color: "var(--danger)" }}>{detectError}</span>
              ) : checkState === "none" ? (
                <span style={{ fontSize: "12px", color: "var(--text-subtle)" }}>
                  No dataset coperceive recognises in this folder.
                </span>
              ) : null}
            </InspectorSection>
            <InspectorSection>
              <button type="button" className="c-primary-wide is-blue" onClick={openDataset} disabled={!detectedIsCurrent}>
                Open dataset
              </button>
              <button type="button" className="c-secondary" onClick={() => void load(root, folder.path)}>
                Open folder
              </button>
            </InspectorSection>
          </>
        ) : null
      }
    >
      <section aria-label={`Browse data folders on ${profile.profile.name}`} style={{ display: "contents" }}>
        {header}
        {roots.length === 0 ? (
          <p className="c-page-note" style={{ color: "#d29922" }}>
            This server has no data folder yet. Add a data folder in its settings, then browse again.
          </p>
        ) : (
          <>
            <PageToolbar>
              {roots.length > 1 ? (
                <select
                  aria-label="Data folder"
                  value={root}
                  disabled={loading}
                  onChange={(event) => void load(event.target.value, "")}
                  className="c-select"
                >
                  {roots.map((candidate) => (
                    <option key={candidate} value={candidate}>
                      {candidate}
                    </option>
                  ))}
                </select>
              ) : null}
              <nav aria-label="Folder path" className="c-crumbs">
                {crumbs.map((crumb, index) => (
                  <span key={`${crumb.path}-${index}`}>
                    {index > 0 ? <span className="c-crumb-sep">/</span> : null}
                    {index === crumbs.length - 1 ? (
                      <strong>{crumb.label}</strong>
                    ) : (
                      <button type="button" className="c-link c-mono" disabled={loading} onClick={() => void load(root, crumb.path)}>
                        {crumb.label}
                      </button>
                    )}
                  </span>
                ))}
              </nav>
              <span className="c-toolbar-end">
                {loading
                  ? "Reading…"
                  : document
                    ? `${rows.length} folder${rows.length === 1 ? "" : "s"} · read in ${Math.round(document.elapsed_ms)} ms · read-only`
                    : ""}
              </span>
            </PageToolbar>
            {error ? (
              <p role="alert" className="c-page-note" style={{ color: "var(--danger)" }}>
                {error}
              </p>
            ) : null}
            {document ? (
              <GridTable
                label="Folders"
                columns={columns}
                rows={rows}
                rowKey={(row) => row.path}
                selectedKey={selected}
                onSelect={(row) => setSelected(row.path)}
                onActivate={(row) => void load(root, row.path)}
                rowHeight={32}
                empty="No folders here."
              />
            ) : null}
            {note ? <p className="c-page-note">{note}</p> : null}
            {skipped.length ? (
              <ul aria-label="Skipped entries" className="c-page-note" style={{ margin: 0 }}>
                {skipped.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : null}
          </>
        )}
      </section>
    </PageLayout>
  );
}
