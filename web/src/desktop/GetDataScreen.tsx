// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import type { CatalogDataset, DryRunResult, DatasetProgressEvent } from "./types";
import PanelState from "./PanelState";
import { dryRunFailureText } from "./dryRun";
import { PANEL_DEFINITIONS } from "./panelStates";
import {
  datasetDryRun,
  startDatasetJob,
  cancelDatasetJob,
  pauseDatasetJob,
  resumeDatasetJob,
  onDatasetProgress,
  pickDataRoot,
  loadDatasetCatalog,
  getPersistentState,
} from "./local";
import { errorText } from "./errors";

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

interface Props {
  onOpenScene: (scenePath: string) => void;
  onOpenImportFolder?: (folderPath?: string) => void;
}

export default function GetDataScreen({ onOpenScene, onOpenImportFolder }: Props) {
  const [catalog, setCatalog] = useState<CatalogDataset[]>([]);
  const [catalogLoading, setCatalogLoading] = useState<boolean>(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [presetBytes, setPresetBytes] = useState<Record<string, number>>({});
  const [presetSizeNote, setPresetSizeNote] = useState<Record<string, string>>({});
  const [selectedDataset, setSelectedDataset] = useState<CatalogDataset | null>(null);
  const [selectedPresetId, setSelectedPresetId] = useState<string>("dev");
  const [dryRun, setDryRun] = useState<DryRunResult | null>(null);
  const [estimating, setEstimating] = useState<boolean>(false);
  const [dryRunError, setDryRunError] = useState<string | null>(null);
  const [acceptedTerms, setAcceptedTerms] = useState<boolean>(false);

  // Job state
  const [jobRunning, setJobRunning] = useState<boolean>(false);
  const [jobStage, setJobStage] = useState<string>("download");
  const [jobPercent, setJobPercent] = useState<number>(0);
  const [jobNote, setJobNote] = useState<string>("");
  const [jobError, setJobError] = useState<string | null>(null);
  const [jobLogs, setJobLogs] = useState<string[]>([]);
  const [showLogModal, setShowLogModal] = useState<boolean>(false);
  const [jobPaused, setJobPaused] = useState<boolean>(false);
  const [resumableJob, setResumableJob] = useState<{ datasetId: string; presetId: string } | null>(null);

  const loadCatalog = async () => {
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      const rows = await loadDatasetCatalog();
      setCatalog(rows);
      const griffin = rows.find((row) => row.id === "griffin");
      if (griffin?.presets) {
        for (const preset of griffin.presets) {
          const key = `griffin:${preset.id}`;
          setPresetSizeNote((prev) => ({ ...prev, [key]: "Checking…" }));
          try {
            const measured = await datasetDryRun("griffin", preset.id);
            setPresetBytes((prev) => ({ ...prev, [key]: measured.download_bytes }));
            setPresetSizeNote((prev) => ({ ...prev, [key]: "" }));
          } catch (err) {
            setPresetSizeNote((prev) => ({
              ...prev,
              [key]: dryRunFailureText(err),
            }));
          }
        }
      }
    } catch (err) {
      setCatalogError(
        errorText(err, "Could not load the dataset list. Close this screen and open Get data again."),
      );
    } finally {
      setCatalogLoading(false);
    }
  };

  useEffect(() => {
    const handleSeed = (event: Event) => {
      const custom = event as CustomEvent<{
        catalog?: CatalogDataset[];
        presetBytes?: Record<string, number>;
      }>;
      if (custom.detail?.catalog !== undefined) {
        setCatalog(custom.detail.catalog);
        setCatalogLoading(false);
        setCatalogError(null);
      }
      if (custom.detail?.presetBytes) {
        setPresetBytes(custom.detail.presetBytes);
      }
    };
    window.addEventListener("coperceive-test-catalog-seed", handleSeed);
    return () => window.removeEventListener("coperceive-test-catalog-seed", handleSeed);
  }, []);

  useEffect(() => {
    void loadCatalog();
    void (async () => {
      const saved = await getPersistentState();
      if (saved?.pending_download?.dataset_id && saved.pending_download.preset_id) {
        setResumableJob({
          datasetId: saved.pending_download.dataset_id,
          presetId: saved.pending_download.preset_id,
        });
      }
    })();
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void (async () => {
      unlisten = await onDatasetProgress((ev: DatasetProgressEvent) => {
        if (ev.event === "stage") {
          setJobStage(ev.stage ?? "download");
          setJobPercent(ev.percent ?? 0);
          setJobNote(ev.message ?? "");
        } else if (ev.event === "log" && ev.message) {
          setJobLogs((prev) => [...prev.slice(-100), ev.message!]);
        } else if (ev.event === "error") {
          setJobError(ev.message ?? "The download did not finish. Try again.");
          setJobRunning(false);
        } else if (ev.event === "complete") {
          setJobRunning(false);
          setJobPaused(false);
          setJobPercent(100);
          setJobNote("Dataset imported.");
          setResumableJob(null);
          if (ev.path) {
            onOpenScene(ev.path);
          }
        } else if (ev.event === "exit") {
          if (ev.code !== 0 && !jobError) {
            setJobError(`Job exited with code ${ev.code}`);
          }
          setJobRunning(false);
        }
      });
    })();
    return () => {
      unlisten?.();
    };
  }, [jobError, onOpenScene]);

  const handleSelectPreset = async (ds: CatalogDataset, presetId: string) => {
    setSelectedDataset(ds);
    setSelectedPresetId(presetId);
    setEstimating(true);
    setDryRun(null);
    setDryRunError(null);
    try {
      const res = await datasetDryRun(ds.id, presetId);
      setDryRun(res);
    } catch (err) {
      setDryRunError(errorText(err));
    } finally {
      setEstimating(false);
    }
  };

  const handleStartDownload = async () => {
    if (!selectedDataset) return;
    if (selectedDataset.requires_terms && !acceptedTerms) {
      alert("Please acknowledge the dataset licence terms before downloading.");
      return;
    }
    setJobRunning(true);
    setJobError(null);
    setJobPercent(5);
    setJobNote("Initiating download…");
    setJobLogs([]);
    setJobPaused(false);
    setResumableJob({ datasetId: selectedDataset.id, presetId: selectedPresetId });

    try {
      await startDatasetJob(selectedDataset.id, selectedPresetId, acceptedTerms);
    } catch (err) {
      setJobError(errorText(err));
      setJobRunning(false);
    }
  };

  const handleCancelJob = async () => {
    await cancelDatasetJob();
    setJobRunning(false);
    setJobNote("Download cancelled.");
  };

  const handlePauseJob = async () => {
    try {
      if (jobPaused) {
        await resumeDatasetJob();
        setJobPaused(false);
        setJobNote("Download resumed.");
      } else {
        await pauseDatasetJob();
        setJobPaused(true);
        setJobNote("Download paused.");
      }
    } catch (err) {
      setJobError(errorText(err));
    }
  };

  const handleResumeJob = (job: { datasetId: string; presetId: string }) => {
    const ds = catalog.find((d) => d.id === job.datasetId);
    if (ds) {
      handleSelectPreset(ds, job.presetId);
      setResumableJob(null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "calc(100vh - 52px)", fontSize: "13px" }}>
      {/* Compact Page Header */}
      <section
        style={{
          padding: "18px 24px 14px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
          borderBottom: "1px solid var(--line, rgba(255,255,255,0.08))",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            <div style={{ fontSize: "11.5px", color: "var(--text-subtle)" }}>
              Local workspace / catalog
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: "12px", flexWrap: "wrap" }}>
              <h2
                style={{
                  margin: 0,
                  fontSize: "20px",
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                  color: "var(--text)",
                }}
              >
                Dataset Catalog
              </h2>
              <span style={{ fontSize: "12.5px", color: "var(--text-tertiary)" }}>
                Download verified cooperative-perception benchmarks directly into your local library.
              </span>
            </div>
          </div>

          {onOpenImportFolder && (
            <button
              type="button"
              onClick={() => onOpenImportFolder()}
              style={{
                padding: "6px 12px",
                borderRadius: "6px",
                border: "1px solid var(--line-elevated, rgba(255,255,255,0.14))",
                backgroundColor: "transparent",
                color: "var(--text)",
                fontSize: "12.5px",
                fontWeight: 500,
                cursor: "pointer",
              }}
            >
              Import local folder…
            </button>
          )}
        </div>

        {resumableJob && !jobRunning && (
          <div
            style={{
              padding: "10px 14px",
              borderRadius: "6px",
              background: "rgba(31, 111, 235, 0.12)",
              border: "1px solid rgba(31, 111, 235, 0.3)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "12px",
              fontSize: "12.5px",
            }}
          >
            <div>
              <strong style={{ color: "var(--text)" }}>
                Resume download of {resumableJob.datasetId} ({resumableJob.presetId})?
              </strong>{" "}
              <span style={{ color: "var(--text-tertiary)" }}>
                Previous download was interrupted before completion.
              </span>
            </div>
            <div style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
              <button
                type="button"
                onClick={() => handleResumeJob(resumableJob)}
                style={{
                  background: "var(--primary, #1f6feb)",
                  color: "#fff",
                  border: "none",
                  borderRadius: "5px",
                  padding: "4px 10px",
                  cursor: "pointer",
                  fontSize: "12px",
                  fontWeight: 500,
                }}
              >
                Resume
              </button>
              <button
                type="button"
                onClick={() => {
                  localStorage.removeItem("coperceive_incomplete_download");
                  setResumableJob(null);
                }}
                style={{
                  background: "transparent",
                  color: "var(--text-tertiary)",
                  border: "1px solid var(--line-elevated)",
                  borderRadius: "5px",
                  padding: "4px 10px",
                  fontSize: "12px",
                  cursor: "pointer",
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        )}

        {catalogError && (
          <p role="alert" style={{ color: "var(--danger)", margin: 0, fontSize: "12.5px" }}>
            {catalogError}
          </p>
        )}
      </section>

      {catalogLoading && (
        <div style={{ padding: "24px" }}>
          <PanelState
            kind="loading"
            title={PANEL_DEFINITIONS.get_data.loading.title}
            description={PANEL_DEFINITIONS.get_data.loading.description}
          />
        </div>
      )}

      {!catalogLoading && catalogError && (
        <div style={{ padding: "24px" }}>
          <PanelState
            kind="error"
            title={PANEL_DEFINITIONS.get_data.error.title}
            description={PANEL_DEFINITIONS.get_data.error.description}
            actionLabel={PANEL_DEFINITIONS.get_data.error.actionLabel}
            onAction={() => void loadCatalog()}
          />
        </div>
      )}

      {!catalogLoading && !catalogError && catalog.length === 0 && (
        <div style={{ padding: "24px" }}>
          <PanelState
            kind="empty"
            title={PANEL_DEFINITIONS.get_data.empty.title}
            description={PANEL_DEFINITIONS.get_data.empty.description}
            actionLabel={PANEL_DEFINITIONS.get_data.empty.actionLabel}
            onAction={() => onOpenImportFolder?.()}
          />
        </div>
      )}

      {/* Tight Catalog Cards Grid with Dense Preset Tables */}
      {!catalogLoading && !catalogError && catalog.length > 0 && (
        <div
          style={{
            padding: "18px 24px",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
            gap: "14px",
            alignItems: "start",
          }}
        >
          {catalog.map((ds) => {
            const isPlanned = ds.status === "planned";
            const isLocal = ds.status === "local-import";
            const isReady = ds.status === "ready";

            return (
              <div
                key={ds.id}
                style={{
                  background: isPlanned ? "var(--sunken)" : "var(--panel)",
                  border: "1px solid var(--line, rgba(255,255,255,0.08))",
                  borderRadius: "8px",
                  display: "flex",
                  flexDirection: "column",
                  overflow: "hidden",
                  opacity: isPlanned ? 0.65 : 1,
                }}
              >
                <div style={{ padding: "14px 16px 12px" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                    <h3 style={{ margin: 0, fontSize: "15px", color: "var(--text)", fontWeight: 600 }}>
                      {ds.name}
                    </h3>
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: "6px",
                        fontSize: "11.5px",
                        fontWeight: 500,
                        color: isReady ? "var(--ok)" : isLocal ? "var(--vehicle)" : "var(--text-subtle)",
                      }}
                    >
                      <span
                        style={{
                          width: "6px",
                          height: "6px",
                          borderRadius: "50%",
                          backgroundColor: isReady ? "#3fb950" : isLocal ? "#f58a1f" : "#6e6e73",
                        }}
                      />
                      {isReady ? "Ready" : isLocal ? "Local import" : "Coming later"}
                    </span>
                  </div>

                  <p style={{ fontSize: "12.5px", color: "var(--text-secondary)", margin: "0 0 10px", lineHeight: 1.45 }}>
                    {ds.description}
                  </p>

                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "auto 1fr",
                      columnGap: "12px",
                      rowGap: "3px",
                      fontSize: "11.5px",
                    }}
                  >
                    <span style={{ color: "var(--text-subtle)" }}>Agents</span>
                    <span style={{ color: "var(--text-secondary)" }}>{ds.agents}</span>
                    <span style={{ color: "var(--text-subtle)" }}>Modalities</span>
                    <span style={{ color: "var(--text-secondary)" }}>{ds.modalities.join(" · ")}</span>
                    <span style={{ color: "var(--text-subtle)" }}>Licence</span>
                    <span style={{ color: "var(--text-secondary)", display: "flex", gap: "8px", flexWrap: "wrap" }}>
                      {ds.licence_url ? (
                        <a
                          href={ds.licence_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "var(--text-secondary)", textDecoration: "underline" }}
                        >
                          {ds.licence}
                        </a>
                      ) : (
                        <span>{ds.licence}</span>
                      )}
                      {ds.paper_url && (
                        <a
                          href={ds.paper_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "var(--primary-hover, #79c0ff)", textDecoration: "none" }}
                        >
                          Paper ↗
                        </a>
                      )}
                      {ds.source_url && (
                        <a
                          href={ds.source_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "var(--primary-hover, #79c0ff)", textDecoration: "none" }}
                        >
                          Source ↗
                        </a>
                      )}
                    </span>
                  </div>

                  {ds.notice && (
                    <p
                      style={{
                        fontSize: "11.5px",
                        color: "var(--text-tertiary)",
                        background: "var(--sunken)",
                        padding: "6px 8px",
                        borderRadius: "5px",
                        margin: "10px 0 0",
                        lineHeight: 1.4,
                      }}
                    >
                      {ds.notice}
                    </p>
                  )}
                </div>

                {/* Dense Preset Table inside the Card */}
                {isReady && ds.presets && (
                  <div style={{ borderTop: "1px solid var(--line, rgba(255,255,255,0.08))" }}>
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "110px 1fr 90px 28px",
                        alignItems: "center",
                        gap: "8px",
                        padding: "5px 16px",
                        backgroundColor: "var(--sunken)",
                        borderBottom: "1px solid var(--line-subtle)",
                        fontSize: "11px",
                        color: "var(--text-subtle)",
                      }}
                    >
                      <span>Preset</span>
                      <span>Description</span>
                      <span style={{ textAlign: "right" }}>Download</span>
                      <span />
                    </div>
                    {ds.presets.map((preset, idx) => {
                      const key = `${ds.id}:${preset.id}`;
                      const sizeLabel =
                        presetBytes[key] != null
                          ? formatBytes(presetBytes[key])
                          : presetSizeNote[key] || "Check size";
                      return (
                        <button
                          key={preset.id}
                          type="button"
                          onClick={() => handleSelectPreset(ds, preset.id)}
                          className="c-table-row"
                          style={{
                            width: "100%",
                            display: "grid",
                            gridTemplateColumns: "110px 1fr 90px 28px",
                            alignItems: "center",
                            gap: "8px",
                            padding: "8px 16px",
                            background: "transparent",
                            border: "none",
                            borderBottom:
                              idx < ds.presets!.length - 1
                                ? "1px solid var(--line-subtle)"
                                : "none",
                            borderRadius: 0,
                            textAlign: "left",
                            cursor: "pointer",
                            fontSize: "12.5px",
                          }}
                        >
                          <strong style={{ fontWeight: 500, color: "var(--text)" }}>
                            {preset.name}
                          </strong>
                          <span
                            style={{
                              fontSize: "12px",
                              color: "var(--text-tertiary)",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {preset.description}
                          </span>
                          <span
                            style={{
                              textAlign: "right",
                              fontFamily: "'Geist Mono', ui-monospace, monospace",
                              fontSize: "11.5px",
                              color: "var(--text-secondary)",
                              fontVariantNumeric: "tabular-nums",
                            }}
                          >
                            {sizeLabel}
                          </span>
                          <span
                            style={{
                              textAlign: "right",
                              color: "var(--primary-hover, #79c0ff)",
                              fontWeight: 500,
                            }}
                          >
                            ↓
                          </span>
                        </button>
                      );
                    })}
                  </div>
                )}

                {isLocal && (
                  <div
                    style={{
                      padding: "10px 16px",
                      borderTop: "1px solid var(--line-subtle)",
                      backgroundColor: "var(--sunken)",
                      display: "flex",
                      justifyContent: "flex-end",
                    }}
                  >
                    <button
                      type="button"
                      onClick={async () => {
                        if (onOpenImportFolder) {
                          const folder = await pickDataRoot();
                          if (folder) onOpenImportFolder(folder);
                        }
                      }}
                      style={{
                        padding: "5px 12px",
                        borderRadius: "5px",
                        background: "transparent",
                        border: "1px solid var(--line-elevated, rgba(255,255,255,0.14))",
                        color: "var(--text)",
                        cursor: "pointer",
                        fontSize: "12px",
                        fontWeight: 500,
                      }}
                    >
                      Import a local copy…
                    </button>
                  </div>
                )}

                {isPlanned && (
                  <div
                    style={{
                      padding: "8px 16px",
                      borderTop: "1px solid var(--line-subtle)",
                      fontSize: "11.5px",
                      color: "var(--text-subtle)",
                    }}
                  >
                    Adapter in preparation
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Preset Dry-run & Progress Modal */}
      {selectedDataset && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.75)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "1rem",
          }}
        >
          <div
            style={{
              background: "var(--panel)",
              borderRadius: "10px",
              border: "1px solid var(--line)",
              maxWidth: "500px",
              width: "100%",
              padding: "1.25rem",
              boxShadow: "var(--shadow)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1rem" }}>
              <div>
                <h3 style={{ margin: 0, fontSize: "15px", fontWeight: 600, color: "var(--text)" }}>
                  Download {selectedDataset.name} ({selectedPresetId})
                </h3>
                <span style={{ fontSize: "12px", color: "var(--text-subtle)" }}>
                  Schema 1 converted dataset download
                </span>
              </div>
              <button
                type="button"
                onClick={() => {
                  setSelectedDataset(null);
                  setDryRun(null);
                }}
                disabled={jobRunning}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "var(--muted)",
                  fontSize: "14px",
                  cursor: "pointer",
                }}
              >
                ✕
              </button>
            </div>

            {estimating && (
              <div style={{ padding: "1.5rem", textAlign: "center", color: "var(--muted)", fontSize: "12.5px" }}>
                Estimating archive sizes and checking volume headroom…
              </div>
            )}

            {dryRunError && (
              <div
                style={{
                  padding: "0.75rem",
                  borderRadius: "6px",
                  background: "rgba(248, 81, 73, 0.12)",
                  color: "var(--danger)",
                  marginBottom: "0.75rem",
                  fontSize: "12.5px",
                }}
              >
                {dryRunError}
              </div>
            )}

            {dryRun && !estimating && !jobRunning && (
              <div style={{ display: "flex", flexDirection: "column", gap: "0.85rem" }}>
                <div
                  style={{
                    background: "var(--sunken)",
                    borderRadius: "6px",
                    padding: "10px 12px",
                    border: "1px solid var(--line-subtle)",
                    display: "grid",
                    gridTemplateColumns: "1fr auto",
                    rowGap: "6px",
                    fontSize: "12.5px",
                  }}
                >
                  <span style={{ color: "var(--text-subtle)" }}>Download size</span>
                  <strong style={{ color: "var(--text)", fontFamily: "'Geist Mono', monospace" }}>
                    {formatBytes(dryRun.download_bytes)}
                  </strong>
                  <span style={{ color: "var(--text-subtle)" }}>Disk space required</span>
                  <strong style={{ color: "var(--text)", fontFamily: "'Geist Mono', monospace" }}>
                    {formatBytes(dryRun.disk_bytes)}
                  </strong>
                  <span style={{ color: "var(--text-subtle)" }}>Free space after</span>
                  <strong
                    style={{
                      color: dryRun.has_headroom ? "var(--ok)" : "var(--danger)",
                      fontFamily: "'Geist Mono', monospace",
                    }}
                  >
                    {formatBytes(dryRun.free_bytes - dryRun.disk_bytes)}
                  </strong>
                </div>

                {!dryRun.has_headroom && (
                  <div
                    style={{
                      padding: "8px 10px",
                      borderRadius: "6px",
                      background: "rgba(248, 81, 73, 0.12)",
                      border: "1px solid rgba(248, 81, 73, 0.3)",
                      color: "var(--danger)",
                      fontSize: "12px",
                    }}
                  >
                    Insufficient disk space. Coperceive requires at least 10% headroom remaining after extraction.
                  </div>
                )}

                {selectedDataset.requires_terms && (
                  <label
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: "8px",
                      fontSize: "12.5px",
                      color: "var(--text)",
                      cursor: "pointer",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={acceptedTerms}
                      onChange={(e) => setAcceptedTerms(e.target.checked)}
                      style={{ marginTop: "2px" }}
                    />
                    <span>
                      I acknowledge and accept the <strong>{selectedDataset.licence}</strong> licence terms for this dataset.
                    </span>
                  </label>
                )}

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "4px" }}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedDataset(null);
                      setDryRun(null);
                    }}
                    style={{
                      padding: "6px 12px",
                      borderRadius: "6px",
                      border: "1px solid var(--line-elevated)",
                      background: "transparent",
                      color: "var(--text)",
                      fontSize: "12.5px",
                      cursor: "pointer",
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleStartDownload}
                    disabled={!dryRun.has_headroom || (selectedDataset.requires_terms && !acceptedTerms)}
                    style={{
                      padding: "6px 14px",
                      borderRadius: "6px",
                      border: "none",
                      background:
                        !dryRun.has_headroom || (selectedDataset.requires_terms && !acceptedTerms)
                          ? "var(--sunken)"
                          : "#1f6feb",
                      color:
                        !dryRun.has_headroom || (selectedDataset.requires_terms && !acceptedTerms)
                          ? "var(--muted)"
                          : "#fff",
                      cursor:
                        !dryRun.has_headroom || (selectedDataset.requires_terms && !acceptedTerms)
                          ? "not-allowed"
                          : "pointer",
                      fontSize: "12.5px",
                      fontWeight: 500,
                    }}
                  >
                    Start Download
                  </button>
                </div>
              </div>
            )}

            {/* Active Download / Conversion Progress Panel (Mock-style 6px progress bar) */}
            {jobRunning && (
              <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: "7px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "12px", fontVariantNumeric: "tabular-nums" }}>
                    <span style={{ color: "var(--text-secondary)", display: "inline-flex", alignItems: "center", gap: "6px" }}>
                      <span style={{ width: "6px", height: "6px", borderRadius: "50%", backgroundColor: "#388bfd" }} />
                      Stage: <strong>{jobStage}</strong>
                    </span>
                    <span style={{ color: "var(--text)", fontWeight: 500, fontFamily: "'Geist Mono', monospace" }}>
                      {jobPercent}%
                    </span>
                  </div>
                  <div
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={jobPercent}
                    style={{
                      height: "6px",
                      background: "var(--line, rgba(255,255,255,0.06))",
                      borderRadius: "3px",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        width: `${jobPercent}%`,
                        height: "100%",
                        background: "#388bfd",
                        transition: "width 0.3s ease",
                      }}
                    />
                  </div>
                  <p style={{ fontSize: "11.5px", color: "var(--text-tertiary)", margin: 0 }}>{jobNote}</p>
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <button
                    type="button"
                    onClick={() => setShowLogModal(true)}
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "var(--primary-hover, #79c0ff)",
                      fontSize: "12px",
                      cursor: "pointer",
                      padding: 0,
                    }}
                  >
                    Show log
                  </button>
                  <div style={{ display: "flex", gap: "6px" }}>
                    <button
                      type="button"
                      onClick={() => void handlePauseJob()}
                      style={{
                        padding: "4px 10px",
                        borderRadius: "5px",
                        border: "1px solid var(--line-elevated)",
                        background: "transparent",
                        color: "var(--text)",
                        cursor: "pointer",
                        fontSize: "12px",
                      }}
                    >
                      {jobPaused ? "Resume" : "Pause"}
                    </button>
                    <button
                      type="button"
                      onClick={handleCancelJob}
                      style={{
                        padding: "4px 10px",
                        borderRadius: "5px",
                        border: "1px solid var(--line-elevated)",
                        background: "transparent",
                        color: "var(--danger)",
                        cursor: "pointer",
                        fontSize: "12px",
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              </div>
            )}

            {jobError && (
              <div style={{ marginTop: "0.75rem" }}>
                <div
                  style={{
                    padding: "8px 10px",
                    borderRadius: "6px",
                    background: "rgba(248, 81, 73, 0.12)",
                    border: "1px solid rgba(248, 81, 73, 0.3)",
                    color: "var(--danger)",
                    fontSize: "12px",
                    marginBottom: "8px",
                  }}
                >
                  {jobError}
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "6px" }}>
                  <button
                    type="button"
                    onClick={() => setShowLogModal(true)}
                    style={{
                      padding: "4px 10px",
                      borderRadius: "5px",
                      border: "1px solid var(--line-elevated)",
                      background: "transparent",
                      color: "var(--text)",
                      fontSize: "12px",
                      cursor: "pointer",
                    }}
                  >
                    Show log
                  </button>
                  <button
                    type="button"
                    onClick={handleStartDownload}
                    style={{
                      padding: "4px 10px",
                      borderRadius: "5px",
                      border: "none",
                      background: "#1f6feb",
                      color: "#fff",
                      fontSize: "12px",
                      cursor: "pointer",
                      fontWeight: 500,
                    }}
                  >
                    Retry
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Log Modal */}
      {showLogModal && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.8)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1100,
            padding: "1rem",
          }}
        >
          <div
            style={{
              background: "var(--panel)",
              borderRadius: "10px",
              border: "1px solid var(--line)",
              maxWidth: "680px",
              width: "100%",
              maxHeight: "80vh",
              display: "flex",
              flexDirection: "column",
              padding: "1rem",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.8rem" }}>
              <h4 style={{ margin: 0, color: "var(--text)", fontSize: "14px" }}>Dataset Job Logs</h4>
              <button
                type="button"
                onClick={() => setShowLogModal(false)}
                style={{ background: "transparent", border: "none", color: "var(--muted)", cursor: "pointer" }}
              >
                ✕
              </button>
            </div>
            <pre
              style={{
                flex: 1,
                overflowY: "auto",
                background: "var(--sunken)",
                padding: "0.75rem",
                borderRadius: "6px",
                fontSize: "11.5px",
                color: "var(--text)",
                fontFamily: "'Geist Mono', monospace",
                margin: 0,
              }}
            >
              {jobLogs.length > 0 ? jobLogs.join("\n") : "No log output recorded."}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
