// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import type { CatalogDataset, DryRunResult, DatasetProgressEvent } from "./types";
import {
  datasetDryRun,
  startDatasetJob,
  cancelDatasetJob,
  onDatasetProgress,
  pickDataRoot,
} from "./local";

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

const CATALOG_DATASETS: CatalogDataset[] = [
  {
    id: "griffin",
    name: "Griffin",
    description: "Cooperative vehicle-to-everything perception with drones and vehicles in CARLA simulation",
    modalities: ["LiDAR", "Camera"],
    agents: "vehicle + drone",
    licence: "MIT",
    licence_url: "https://huggingface.co/datasets/coperceive/griffin/blob/main/LICENSE",
    source_url: "https://huggingface.co/datasets/coperceive/griffin",
    paper_url: "https://arxiv.org/abs/2403.02321",
    status: "ready",
    requires_terms: true,
    presets: [
      {
        id: "dev",
        name: "Dev tier: metadata + LiDAR (~240 MB)",
        description: "Lightweight subset with downsampled LiDAR and vehicle-drone trajectory metadata",
        estimated_download_bytes: 251658240,
        estimated_disk_bytes: 524288000,
      },
      {
        id: "one-scene",
        name: "One scene with images",
        description: "Complete Town03-000 evaluation scene with all 9 cameras, LiDAR, and vector map",
        estimated_download_bytes: 817889280,
        estimated_disk_bytes: 1610612736,
      },
      {
        id: "full",
        name: "Full 25 m subset (~167 GB)",
        description: "Full cooperative 25m benchmark scenes across all towns and sensor feeds",
        estimated_download_bytes: 179307380736,
        estimated_disk_bytes: 268435456000,
      },
    ],
  },
  {
    id: "opv2v",
    name: "OPV2V",
    description: "Open cooperative perception dataset for vehicle-to-vehicle autonomous driving",
    modalities: ["LiDAR", "Camera"],
    agents: "vehicle + vehicle",
    licence: "unknown",
    licence_url: "https://mobility-lab.seas.ucla.edu/opv2v/",
    source_url: "https://github.com/DerrickXuNu/OPV2V",
    paper_url: "https://arxiv.org/abs/2109.07644",
    status: "local-import",
    notice: "Data files are hosted on third-party mirrors and lack an explicit permissive data licence. Use 'Import from folder…' to import a local copy.",
  },
  {
    id: "v2xset",
    name: "V2XSet",
    description: "V2X cooperative perception benchmark featuring vehicle-infrastructure collaboration",
    modalities: ["LiDAR", "Camera"],
    agents: "vehicle + infrastructure",
    licence: "unknown",
    licence_url: "https://github.com/DerrickXuNu/v2x-vit",
    source_url: "https://github.com/DerrickXuNu/v2x-vit",
    paper_url: "https://arxiv.org/abs/2203.10638",
    status: "local-import",
    notice: "Data files are hosted on third-party mirrors and lack an explicit permissive data licence. Use 'Import from folder…' to import a local copy.",
  },
  {
    id: "v2v4real",
    name: "V2V4Real",
    description: "Real-world dataset for vehicle-to-vehicle cooperative perception with multiple connected vehicles",
    modalities: ["LiDAR", "Camera"],
    agents: "vehicle + vehicle",
    licence: "CC-BY-NC-SA-4.0",
    licence_url: "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    source_url: "https://github.com/ucla-mobility/V2V4Real",
    paper_url: "https://arxiv.org/abs/2303.07601",
    status: "planned",
    notice: "Planned for a future release.",
  },
];

interface Props {
  onOpenScene: (scenePath: string) => void;
  onOpenImportFolder?: (folderPath?: string) => void;
}

export default function GetDataScreen({ onOpenScene, onOpenImportFolder }: Props) {
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
  const [resumableJob, setResumableJob] = useState<{ datasetId: string; presetId: string } | null>(null);

  // Check saved incomplete download on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem("coperceive_incomplete_download");
      if (saved) {
        setResumableJob(JSON.parse(saved));
      }
    } catch {}
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
          setJobError(ev.message ?? "An error occurred during dataset processing.");
          setJobRunning(false);
          try {
            localStorage.removeItem("coperceive_incomplete_download");
          } catch {}
        } else if (ev.event === "complete") {
          setJobRunning(false);
          setJobPercent(100);
          setJobNote("Dataset successfully imported!");
          try {
            localStorage.removeItem("coperceive_incomplete_download");
          } catch {}
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
      setDryRunError(err instanceof Error ? err.message : String(err));
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
    try {
      localStorage.setItem(
        "coperceive_incomplete_download",
        JSON.stringify({ datasetId: selectedDataset.id, presetId: selectedPresetId }),
      );
    } catch {}

    try {
      await startDatasetJob(selectedDataset.id, selectedPresetId, acceptedTerms);
    } catch (err) {
      setJobError(err instanceof Error ? err.message : String(err));
      setJobRunning(false);
    }
  };

  const handleCancelJob = async () => {
    await cancelDatasetJob();
    setJobRunning(false);
    setJobNote("Download cancelled.");
  };

  const handleResumeJob = (job: { datasetId: string; presetId: string }) => {
    const ds = CATALOG_DATASETS.find((d) => d.id === job.datasetId);
    if (ds) {
      handleSelectPreset(ds, job.presetId);
      setResumableJob(null);
    }
  };

  return (
    <div style={{ padding: "1.5rem", maxWidth: "980px", margin: "0 auto" }}>
      {resumableJob && !jobRunning && (
        <div
          style={{
            marginBottom: "1.5rem",
            padding: "1rem",
            borderRadius: "8px",
            background: "rgba(217, 72, 43, 0.15)",
            border: "1px solid rgba(217, 72, 43, 0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div>
            <strong style={{ color: "#f5f5f7", display: "block" }}>
              Resume download of {resumableJob.datasetId} ({resumableJob.presetId})?
            </strong>
            <span style={{ fontSize: "0.85rem", color: "#a1a1a6" }}>
              Previous download was interrupted before completion.
            </span>
          </div>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <button
              type="button"
              onClick={() => handleResumeJob(resumableJob)}
              style={{
                background: "#D9482B",
                color: "#fff",
                border: "none",
                borderRadius: "6px",
                padding: "0.4rem 0.8rem",
                cursor: "pointer",
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
                color: "#a1a1a6",
                border: "1px solid rgba(255, 255, 255, 0.2)",
                borderRadius: "6px",
                padding: "0.4rem 0.8rem",
                cursor: "pointer",
              }}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <div style={{ marginBottom: "2rem" }}>
        <h2 style={{ fontSize: "1.4rem", fontWeight: 600, color: "#f5f5f7", marginBottom: "0.4rem" }}>
          Dataset Catalog
        </h2>
        <p style={{ color: "#a1a1a6", fontSize: "0.9rem", margin: 0 }}>
          Download verified cooperative-perception benchmarks directly into your local library.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "1rem" }}>
        {CATALOG_DATASETS.map((ds) => {
          const isPlanned = ds.status === "planned";
          const isLocal = ds.status === "local-import";
          const isReady = ds.status === "ready";

          return (
            <div
              key={ds.id}
              style={{
                background: isPlanned ? "rgba(255, 255, 255, 0.02)" : "rgba(255, 255, 255, 0.05)",
                border: "1px solid rgba(255, 255, 255, 0.1)",
                borderRadius: "10px",
                padding: "1.2rem",
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                opacity: isPlanned ? 0.6 : 1,
              }}
            >
              <div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.5rem" }}>
                  <h3 style={{ margin: 0, fontSize: "1.15rem", color: "#f5f5f7", fontWeight: 600 }}>{ds.name}</h3>
                  <span
                    style={{
                      fontSize: "0.75rem",
                      padding: "0.15rem 0.5rem",
                      borderRadius: "12px",
                      background: isReady ? "rgba(40, 120, 220, 0.2)" : isLocal ? "rgba(255, 180, 0, 0.15)" : "rgba(255, 255, 255, 0.1)",
                      color: isReady ? "#58a6ff" : isLocal ? "#e3b341" : "#8b949e",
                      fontWeight: 500,
                    }}
                  >
                    {isReady ? "Ready" : isLocal ? "Local import" : "Coming later"}
                  </span>
                </div>

                <p style={{ fontSize: "0.85rem", color: "#a1a1a6", marginBottom: "0.8rem", lineHeight: 1.4 }}>
                  {ds.description}
                </p>

                <div style={{ fontSize: "0.8rem", color: "#8b949e", display: "flex", flexDirection: "column", gap: "0.3rem", marginBottom: "1rem" }}>
                  <div><strong>Agents:</strong> {ds.agents}</div>
                  <div><strong>Modalities:</strong> {ds.modalities.join(", ")}</div>
                  <div>
                    <strong>Licence:</strong>{" "}
                    {ds.licence_url ? (
                      <a href={ds.licence_url} target="_blank" rel="noreferrer" style={{ color: "#58a6ff", textDecoration: "none" }}>
                        {ds.licence}
                      </a>
                    ) : (
                      ds.licence
                    )}
                  </div>
                  <div style={{ display: "flex", gap: "0.8rem", marginTop: "0.2rem" }}>
                    {ds.paper_url && (
                      <a href={ds.paper_url} target="_blank" rel="noreferrer" style={{ color: "#58a6ff", textDecoration: "none" }}>
                        Paper ↗
                      </a>
                    )}
                    {ds.source_url && (
                      <a href={ds.source_url} target="_blank" rel="noreferrer" style={{ color: "#58a6ff", textDecoration: "none" }}>
                        Source ↗
                      </a>
                    )}
                  </div>
                </div>

                {ds.notice && (
                  <p style={{ fontSize: "0.75rem", color: "#8b949e", background: "rgba(0, 0, 0, 0.2)", padding: "0.5rem", borderRadius: "6px" }}>
                    {ds.notice}
                  </p>
                )}
              </div>

              <div style={{ marginTop: "1rem", paddingTop: "0.8rem", borderTop: "1px solid rgba(255, 255, 255, 0.08)" }}>
                {isReady && ds.presets && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <span style={{ fontSize: "0.8rem", color: "#a1a1a6" }}>Presets:</span>
                    </div>
                    {ds.presets.map((preset) => (
                      <button
                        key={preset.id}
                        type="button"
                        onClick={() => handleSelectPreset(ds, preset.id)}
                        style={{
                          background: "rgba(255, 255, 255, 0.08)",
                          border: "1px solid rgba(255, 255, 255, 0.12)",
                          borderRadius: "6px",
                          padding: "0.5rem 0.75rem",
                          color: "#f5f5f7",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "space-between",
                          textAlign: "left",
                          fontSize: "0.8rem",
                        }}
                      >
                        <div>
                          <strong>{preset.name}</strong>
                          <div style={{ fontSize: "0.75rem", color: "#8b949e" }}>{preset.description}</div>
                        </div>
                        <span style={{ fontSize: "1rem", marginLeft: "0.5rem" }}>↓</span>
                      </button>
                    ))}
                  </div>
                )}

                {isLocal && (
                  <button
                    type="button"
                    onClick={async () => {
                      if (onOpenImportFolder) {
                        const folder = await pickDataRoot();
                        if (folder) onOpenImportFolder(folder);
                      }
                    }}
                    style={{
                      width: "100%",
                      padding: "0.5rem",
                      borderRadius: "6px",
                      background: "rgba(255, 255, 255, 0.08)",
                      border: "1px solid rgba(255, 255, 255, 0.15)",
                      color: "#f5f5f7",
                      cursor: "pointer",
                      fontSize: "0.85rem",
                      fontWeight: 500,
                    }}
                  >
                    Import a local copy…
                  </button>
                )}

                {isPlanned && (
                  <div style={{ textAlign: "center", fontSize: "0.8rem", color: "#8b949e", padding: "0.4rem" }}>
                    Coming later
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Preset Dry-run Modal */}
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
              background: "#1c1c1e",
              borderRadius: "12px",
              border: "1px solid rgba(255, 255, 255, 0.15)",
              maxWidth: "540px",
              width: "100%",
              padding: "1.5rem",
              boxShadow: "0 20px 40px rgba(0, 0, 0, 0.5)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1rem" }}>
              <div>
                <h3 style={{ margin: 0, fontSize: "1.2rem", color: "#f5f5f7" }}>
                  Download {selectedDataset.name} ({selectedPresetId})
                </h3>
                <span style={{ fontSize: "0.85rem", color: "#8b949e" }}>
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
                  color: "#8b949e",
                  fontSize: "1.2rem",
                  cursor: "pointer",
                }}
              >
                ✕
              </button>
            </div>

            {estimating && (
              <div style={{ padding: "2rem", textAlign: "center", color: "#a1a1a6" }}>
                Estimating archive sizes and checking volume headroom…
              </div>
            )}

            {dryRunError && (
              <div style={{ padding: "1rem", borderRadius: "8px", background: "rgba(255, 60, 60, 0.15)", color: "#ff6b6b", marginBottom: "1rem" }}>
                {dryRunError}
              </div>
            )}

            {dryRun && !estimating && !jobRunning && (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                <div style={{ background: "rgba(255, 255, 255, 0.04)", borderRadius: "8px", padding: "1rem" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.4rem", fontSize: "0.9rem" }}>
                    <span style={{ color: "#a1a1a6" }}>Download size:</span>
                    <strong style={{ color: "#f5f5f7" }}>{formatBytes(dryRun.download_bytes)}</strong>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.4rem", fontSize: "0.9rem" }}>
                    <span style={{ color: "#a1a1a6" }}>Disk space required:</span>
                    <strong style={{ color: "#f5f5f7" }}>{formatBytes(dryRun.disk_bytes)}</strong>
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.9rem" }}>
                    <span style={{ color: "#a1a1a6" }}>Free space after:</span>
                    <strong style={{ color: dryRun.has_headroom ? "#58a6ff" : "#ff6b6b" }}>
                      {formatBytes(dryRun.free_bytes - dryRun.disk_bytes)}
                    </strong>
                  </div>
                </div>

                {!dryRun.has_headroom && (
                  <div
                    style={{
                      padding: "0.8rem",
                      borderRadius: "6px",
                      background: "rgba(255, 69, 58, 0.15)",
                      border: "1px solid rgba(255, 69, 58, 0.3)",
                      color: "#ff6b6b",
                      fontSize: "0.85rem",
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
                      gap: "0.5rem",
                      fontSize: "0.85rem",
                      color: "#f5f5f7",
                      cursor: "pointer",
                      padding: "0.5rem 0",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={acceptedTerms}
                      onChange={(e) => setAcceptedTerms(e.target.checked)}
                      style={{ marginTop: "0.2rem" }}
                    />
                    <span>
                      I acknowledge and accept the <strong>{selectedDataset.licence}</strong> licence terms for this dataset.
                    </span>
                  </label>
                )}

                <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.75rem", marginTop: "0.5rem" }}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedDataset(null);
                      setDryRun(null);
                    }}
                    style={{
                      padding: "0.5rem 1rem",
                      borderRadius: "6px",
                      border: "1px solid rgba(255, 255, 255, 0.2)",
                      background: "transparent",
                      color: "#f5f5f7",
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
                      padding: "0.5rem 1.2rem",
                      borderRadius: "6px",
                      border: "none",
                      background: !dryRun.has_headroom || (selectedDataset.requires_terms && !acceptedTerms)
                        ? "rgba(255, 255, 255, 0.1)"
                        : "#1f6feb",
                      color: "#fff",
                      cursor: !dryRun.has_headroom || (selectedDataset.requires_terms && !acceptedTerms)
                        ? "not-allowed"
                        : "pointer",
                      fontWeight: 500,
                    }}
                  >
                    Start Download
                  </button>
                </div>
              </div>
            )}

            {/* Active Download / Conversion Progress Panel */}
            {jobRunning && (
              <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem", marginBottom: "0.4rem" }}>
                    <span style={{ color: "#a1a1a6" }}>
                      Stage: <strong>{jobStage}</strong>
                    </span>
                    <span style={{ color: "#f5f5f7", fontWeight: 600 }}>{jobPercent}%</span>
                  </div>
                  <div
                    role="progressbar"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={jobPercent}
                    style={{ height: "8px", background: "rgba(255, 255, 255, 0.1)", borderRadius: "4px", overflow: "hidden" }}
                  >
                    <div
                      style={{
                        width: `${jobPercent}%`,
                        height: "100%",
                        background: "#1f6feb",
                        transition: "width 0.3s ease",
                      }}
                    />
                  </div>
                  <p style={{ fontSize: "0.8rem", color: "#8b949e", marginTop: "0.4rem" }}>{jobNote}</p>
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <button
                    type="button"
                    onClick={() => setShowLogModal(true)}
                    style={{
                      background: "transparent",
                      border: "none",
                      color: "#58a6ff",
                      fontSize: "0.85rem",
                      cursor: "pointer",
                      padding: 0,
                    }}
                  >
                    Show log
                  </button>
                  <button
                    type="button"
                    onClick={handleCancelJob}
                    style={{
                      padding: "0.4rem 0.8rem",
                      borderRadius: "6px",
                      border: "1px solid rgba(255, 255, 255, 0.2)",
                      background: "transparent",
                      color: "#ff6b6b",
                      cursor: "pointer",
                      fontSize: "0.85rem",
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {jobError && (
              <div style={{ marginTop: "1rem" }}>
                <div
                  style={{
                    padding: "0.8rem",
                    borderRadius: "6px",
                    background: "rgba(255, 69, 58, 0.15)",
                    border: "1px solid rgba(255, 69, 58, 0.3)",
                    color: "#ff6b6b",
                    fontSize: "0.85rem",
                    marginBottom: "0.8rem",
                  }}
                >
                  {jobError}
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
                  <button
                    type="button"
                    onClick={() => setShowLogModal(true)}
                    style={{
                      padding: "0.4rem 0.8rem",
                      borderRadius: "6px",
                      border: "1px solid rgba(255, 255, 255, 0.2)",
                      background: "transparent",
                      color: "#f5f5f7",
                      cursor: "pointer",
                    }}
                  >
                    Show log
                  </button>
                  <button
                    type="button"
                    onClick={handleStartDownload}
                    style={{
                      padding: "0.4rem 0.8rem",
                      borderRadius: "6px",
                      border: "none",
                      background: "#1f6feb",
                      color: "#fff",
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
              background: "#141414",
              borderRadius: "10px",
              border: "1px solid rgba(255, 255, 255, 0.15)",
              maxWidth: "680px",
              width: "100%",
              maxHeight: "80vh",
              display: "flex",
              flexDirection: "column",
              padding: "1rem",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.8rem" }}>
              <h4 style={{ margin: 0, color: "#f5f5f7" }}>Dataset Job Logs</h4>
              <button
                type="button"
                onClick={() => setShowLogModal(false)}
                style={{ background: "transparent", border: "none", color: "#a1a1a6", cursor: "pointer" }}
              >
                ✕
              </button>
            </div>
            <pre
              style={{
                flex: 1,
                overflowY: "auto",
                background: "#0a0a0a",
                padding: "0.75rem",
                borderRadius: "6px",
                fontSize: "0.8rem",
                color: "#e6edf3",
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
