// SPDX-License-Identifier: Apache-2.0
/**
 * Browse a remote server's data folders.
 *
 * Metadata only: the app runs `coperceive remote scan` over the reused SSH
 * connection and shows folder listings, detected scenes and datasets, and the
 * plain-language notes when a bound stopped the scan. Opening a scene for
 * playback needs the confirmed job-submission step, so the Open button is
 * disabled and explains that nothing was started.
 */
import { useEffect, useMemo, useState } from "react";
import {
  browseCrumbs,
  browseRows,
  OPEN_BLOCKED_REASON,
  parentBrowsePath,
  relativeBrowsePath,
  scanSummary,
  selectionLabel,
  skippedMessages,
  truncatedMessage,
} from "./browse";
import type { ScanDocument, ServerEntry } from "./hpc";
import { remoteScan } from "./local";
import ClusterDatasetView from "./ClusterDatasetView";
import {
  datasetDetect,
  formatDetectBannerTitle,
  formatLicenceInfo,
  type ClusterDetectDocument,
} from "./clusterDatasets";

const card = {
  backgroundColor: "rgba(28, 28, 30, 0.85)",
  border: "1px solid rgba(255, 255, 255, 0.14)",
  borderRadius: "14px",
  padding: "1.25rem",
  marginTop: "1rem",
  boxShadow: "0 20px 50px rgba(0, 0, 0, 0.45)",
} as const;

const secondaryButton = {
  padding: "0.4rem 0.7rem",
  borderRadius: "6px",
  border: "1px solid rgba(255, 255, 255, 0.18)",
  backgroundColor: "transparent",
  color: "#f5f5f7",
  fontSize: "0.8rem",
  cursor: "pointer",
} as const;

const primaryButton = {
  padding: "0.45rem 0.9rem",
  borderRadius: "6px",
  border: "none",
  backgroundColor: "#1f6feb",
  color: "#ffffff",
  fontSize: "0.82rem",
  fontWeight: 600,
  cursor: "pointer",
} as const;

const badge = {
  display: "inline-block",
  marginLeft: "0.4rem",
  padding: "0.05rem 0.4rem",
  borderRadius: "999px",
  fontSize: "0.68rem",
  letterSpacing: "0.02em",
  textTransform: "uppercase",
} as const;

export default function HpcBrowse({
  profile,
  onClose,
  onOpenDataset,
}: {
  profile: ServerEntry;
  onClose: () => void;
  onOpenDataset?: (detected: ClusterDetectDocument) => void;
}) {
  const roots = profile.profile.data_roots.filter((root) => root.trim().length > 0);
  const [root, setRoot] = useState<string>(roots[0] ?? "");
  const [document, setDocument] = useState<ScanDocument | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  // Cluster dataset detect state
  const [detectedDataset, setDetectedDataset] = useState<ClusterDetectDocument | null>(null);
  const [detectLoading, setDetectLoading] = useState(false);
  const [detectError, setDetectError] = useState("");
  const [viewingDataset, setViewingDataset] = useState<ClusterDetectDocument | null>(null);

  const firstRoot = roots[0] ?? "";
  useEffect(() => {
    setRoot(firstRoot);
    setDocument(null);
    setError("");
    setSelected(null);
    if (firstRoot) {
      void remoteScan(profile.id, firstRoot, "")
        .then((result) => {
          setDocument(result);
        })
        .catch((e: unknown) => {
          setError(e instanceof Error ? e.message : String(e));
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.id]);

  const load = async (nextRoot: string, nextPath: string) => {
    setLoading(true);
    setError("");
    try {
      const result = await remoteScan(profile.id, nextRoot, nextPath);
      setRoot(nextRoot);
      setDocument(result);
      setSelected(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDocument(null);
    } finally {
      setLoading(false);
    }
  };

  const current = relativeBrowsePath(document);
  const rows = browseRows(document);
  const crumbs = browseCrumbs(root, current);
  const note = truncatedMessage(document);
  const skipped = skippedMessages(document);
  const summary = scanSummary(document);
  const selectedText = selectionLabel(document, selected);

  const currentFolderFullPath = useMemo(() => {
    if (!root) return "";
    return current ? `${root.replace(/\/+$/, "")}/${current.replace(/^\/+/, "")}` : root;
  }, [root, current]);

  // Debounced cluster dataset detection
  useEffect(() => {
    if (!currentFolderFullPath || loading) {
      setDetectedDataset(null);
      return;
    }
    let active = true;
    setDetectLoading(true);
    setDetectError("");

    const timer = setTimeout(() => {
      datasetDetect(profile.id, currentFolderFullPath)
        .then((res) => {
          if (!active) return;
          setDetectLoading(false);
          if (res.status === "ok") {
            setDetectedDataset(res);
          } else if (res.status === "error") {
            setDetectedDataset(null);
            setDetectError(res.reason || "The cluster refused the dataset detection request.");
          } else {
            setDetectedDataset(null);
          }
        })
        .catch((e: unknown) => {
          if (!active) return;
          setDetectLoading(false);
          setDetectedDataset(null);
          setDetectError(e instanceof Error ? e.message : String(e));
        });
    }, 150);

    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [profile.id, currentFolderFullPath, loading]);

  if (viewingDataset) {
    return (
      <ClusterDatasetView
        profile={profile}
        dataset={viewingDataset}
        onBack={() => setViewingDataset(null)}
        onClose={onClose}
      />
    );
  }

  return (
    <section aria-label={`Browse data folders on ${profile.profile.name}`} style={card}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h4 style={{ margin: 0, fontSize: "0.95rem", color: "#f5f5f7" }}>
            Browse data folders on {profile.profile.name}
          </h4>
          <p style={{ margin: "0.2rem 0 0", fontSize: "0.75rem", color: "#8b949e" }}>
            Read-only and metadata only: folder names, scenes and datasets. No scene data is
            downloaded and nothing is started on the cluster.
          </p>
        </div>
        <button type="button" style={secondaryButton} onClick={onClose}>
          Close
        </button>
      </div>

      {roots.length === 0 ? (
        <p style={{ fontSize: "0.82rem", color: "#ffb86b", marginTop: "0.75rem" }}>
          This server has no data folder yet. Add a data folder in its settings, then browse again.
        </p>
      ) : (
        <>
          <div
            role="group"
            aria-label="Data folders"
            style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap", marginTop: "0.75rem" }}
          >
            {roots.map((candidate) => (
              <button
                key={candidate}
                type="button"
                style={{
                  ...secondaryButton,
                  borderColor:
                    candidate === root ? "#1f6feb" : "rgba(255, 255, 255, 0.18)",
                  backgroundColor:
                    candidate === root ? "rgba(31, 111, 235, 0.25)" : "transparent",
                  fontFamily: "monospace",
                  fontSize: "0.75rem",
                }}
                disabled={loading}
                onClick={() => void load(candidate, "")}
              >
                {candidate}
              </button>
            ))}
          </div>

          <nav aria-label="Folder path" style={{ marginTop: "0.65rem", fontSize: "0.8rem" }}>
            {crumbs.map((crumb, index) => (
              <span key={`${crumb.path}-${index}`}>
                {index > 0 ? <span style={{ color: "#6e7681" }}> / </span> : null}
                {index === crumbs.length - 1 ? (
                  <strong style={{ color: "#f5f5f7" }}>{crumb.label}</strong>
                ) : (
                  <button
                    type="button"
                    style={{
                      background: "none",
                      border: "none",
                      padding: 0,
                      color: "#58a6ff",
                      cursor: "pointer",
                      fontSize: "0.8rem",
                    }}
                    onClick={() => void load(root, crumb.path)}
                  >
                    {crumb.label}
                  </button>
                )}
              </span>
            ))}
          </nav>

          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.5rem" }}>
            <button
              type="button"
              style={secondaryButton}
              disabled={loading || current === ""}
              onClick={() => void load(root, parentBrowsePath(current))}
            >
              Up one folder
            </button>
            <span role="status" style={{ fontSize: "0.75rem", color: "#8b949e" }}>
              {loading ? "Asking the cluster for folder names (a few seconds)…" : detectLoading ? "Detecting dataset format…" : summary}
            </span>
          </div>

          {error ? (
            <div
              role="alert"
              style={{
                marginTop: "0.6rem",
                padding: "0.5rem 0.7rem",
                borderRadius: "8px",
                backgroundColor: "rgba(255, 98, 89, 0.12)",
                border: "1px solid rgba(255, 98, 89, 0.35)",
                color: "#ff6259",
                fontSize: "0.82rem",
              }}
            >
              {error}
            </div>
          ) : null}

          {note ? (
            <div
              role="status"
              style={{
                marginTop: "0.6rem",
                padding: "0.5rem 0.7rem",
                borderRadius: "8px",
                backgroundColor: "rgba(210, 153, 34, 0.12)",
                border: "1px solid rgba(210, 153, 34, 0.35)",
                color: "#e3b341",
                fontSize: "0.82rem",
              }}
            >
              {note}
            </div>
          ) : null}

          {skipped.length > 0 ? (
            <ul
              aria-label="Skipped entries"
              style={{ margin: "0.5rem 0 0", paddingLeft: "1.1rem", color: "#8b949e", fontSize: "0.75rem" }}
            >
              {skipped.map((message) => (
                <li key={message} style={{ marginBottom: "0.2rem" }}>
                  {message}
                </li>
              ))}
            </ul>
          ) : null}

          {document && rows.length === 0 && !loading && !error ? (
            <p style={{ fontSize: "0.82rem", color: "#8b949e", marginTop: "0.65rem" }}>
              This folder has no subfolders.
            </p>
          ) : null}

          {/* Cluster dataset detected banner */}
          {detectedDataset && detectedDataset.status === "ok" ? (
            <div
              role="region"
              aria-label="Detected cluster dataset"
              style={{
                marginTop: "0.75rem",
                padding: "0.75rem 1rem",
                borderRadius: "10px",
                backgroundColor: "rgba(31, 111, 235, 0.15)",
                border: "1px solid #1f6feb",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                gap: "1rem",
                flexWrap: "wrap",
              }}
            >
              <div>
                <strong style={{ fontSize: "0.95rem", color: "#f5f5f7" }}>
                  {formatDetectBannerTitle(detectedDataset)}
                </strong>
                <div style={{ marginTop: "0.2rem", fontSize: "0.76rem", color: "#c9d1d9" }}>
                  <span>{formatLicenceInfo(detectedDataset.licence).statusText}</span>
                  {formatLicenceInfo(detectedDataset.licence).links.length > 0 ? (
                    <span style={{ marginLeft: "0.4rem" }}>
                      {formatLicenceInfo(detectedDataset.licence).links.map((link, idx) => (
                        <a
                          key={link}
                          href={link}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "#58a6ff", textDecoration: "underline", marginRight: "0.35rem" }}
                        >
                          [{idx + 1}]
                        </a>
                      ))}
                    </span>
                  ) : null}
                </div>
              </div>
              <button
                type="button"
                style={{
                  ...primaryButton,
                  backgroundColor: "#1f6feb",
                  padding: "0.45rem 1rem",
                  fontSize: "0.85rem",
                }}
                onClick={() => {
                  if (onOpenDataset) {
                    onOpenDataset(detectedDataset);
                  }
                  setViewingDataset(detectedDataset);
                }}
              >
                Open dataset
              </button>
            </div>
          ) : null}

          {detectError ? (
            <div
              role="alert"
              style={{
                marginTop: "0.6rem",
                padding: "0.5rem 0.7rem",
                borderRadius: "8px",
                backgroundColor: "rgba(255, 98, 89, 0.12)",
                border: "1px solid rgba(255, 98, 89, 0.35)",
                color: "#ff6259",
                fontSize: "0.8rem",
              }}
            >
              {detectError}
            </div>
          ) : null}

          <ul aria-label="Folders" style={{ listStyle: "none", padding: 0, margin: "0.6rem 0 0" }}>
            {rows.map((row) => (
              <li key={row.path} style={{ marginBottom: "0.25rem" }}>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "0.5rem",
                    padding: "0.35rem 0.5rem",
                    borderRadius: "8px",
                    border:
                      selected === row.path
                        ? "1px solid #1f6feb"
                        : "1px solid rgba(255, 255, 255, 0.08)",
                    backgroundColor:
                      selected === row.path ? "rgba(31, 111, 235, 0.18)" : "transparent",
                  }}
                >
                  <button
                    type="button"
                    style={{
                      flex: "1 1 auto",
                      textAlign: "left",
                      background: "none",
                      border: "none",
                      padding: 0,
                      color: "#f5f5f7",
                      fontSize: "0.85rem",
                      cursor: "pointer",
                    }}
                    aria-label={`${row.kind} ${row.name}`}
                    onClick={() => {
                      if (row.kind === "folder" || row.kind === "dataset") {
                        // row.path is already relative to the data root (the scan
                        // reports root-relative paths even for nested rows), so
                        // send it unchanged; using the basename here would drop
                        // the parent for a folder listed below the current one.
                        void load(root, row.path);
                      } else {
                        setSelected(row.path);
                      }
                    }}
                  >
                    {row.name}
                    <span
                      style={{
                        ...badge,
                        backgroundColor:
                          row.kind === "scene"
                            ? "rgba(63, 185, 80, 0.2)"
                            : row.kind === "dataset"
                              ? "rgba(88, 166, 255, 0.2)"
                              : "rgba(255, 255, 255, 0.08)",
                        color:
                          row.kind === "scene"
                            ? "#7ee787"
                            : row.kind === "dataset"
                              ? "#79c0ff"
                              : "#8b949e",
                      }}
                    >
                      {row.kind}
                    </span>
                    <span style={{ marginLeft: "0.5rem", color: "#8b949e", fontSize: "0.75rem" }}>
                      {row.detail}
                    </span>
                  </button>
                  {row.kind !== "folder" ? (
                    <button
                      type="button"
                      style={secondaryButton}
                      aria-pressed={selected === row.path}
                      onClick={() => setSelected(row.path)}
                    >
                      {selected === row.path ? "Selected" : "Select"}
                    </button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>

          {selectedText ? (
            <div style={{ marginTop: "0.75rem", borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: "0.6rem" }}>
              <div style={{ fontSize: "0.82rem", color: "#f5f5f7" }}>{selectedText} selected.</div>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.4rem" }}>
                <button type="button" style={{ ...secondaryButton, opacity: 0.55, cursor: "not-allowed" }} disabled>
                  Open scene (not available yet)
                </button>
                <span style={{ fontSize: "0.75rem", color: "#8b949e" }}>{OPEN_BLOCKED_REASON}</span>
              </div>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
