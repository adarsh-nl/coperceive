// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import type { CatalogDataset, DryRunResult, DatasetProgressEvent } from "./types";
import PanelState from "./PanelState";
import { dryRunFailureText } from "./dryRun";
import { PANEL_DEFINITIONS } from "./panelStates";
import {
  datasetDryRun,
  ensureDemoScene,
  cancelDemoScene,
  onDemoProgress,
  startDatasetJob,
  cancelDatasetJob,
  pauseDatasetJob,
  resumeDatasetJob,
  onDatasetProgress,
  pickDataRoot,
  loadDatasetCatalog,
  getPersistentState,
  getStorageInfo,
} from "./local";
import { errorText } from "./errors";
import { DEMO_APPROX_SIZE_LABEL, formatDemoSize, runDemoOpenFlow } from "./demoMeta";
import {
  GridTable,
  InspectorSection,
  PageLayout,
  useDefaultSelection,
  usePageHeader,
  type GridColumn,
} from "./components/Page";
import { datasetJobs, subscribeDatasetJobs, type DatasetJobs } from "./jobsStore";

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

interface Props {
  onOpenScene: (scenePath: string) => void;
  onOpenImportFolder?: (folderPath?: string) => void;
  /** "Free up space" goes to the Library, where scenes can be removed. */
  onOpenLibrary?: () => void;
  initialDatasetId?: string;
  initialPresetId?: string;
}

const datasetKey = (ds: CatalogDataset) => ds.id;

/** What the Availability column says: only from the catalog and real cluster state. */
export function availabilityOf(
  ds: CatalogDataset,
  presetBytes: Record<string, number>,
  clusterJobs: readonly DatasetJobs[],
): { label: string; hint: string; color: string; dot: string } {
  if (ds.status === "ready") {
    const sizes = (ds.presets ?? [])
      .map((preset) => presetBytes[`${ds.id}:${preset.id}`])
      .filter((bytes): bytes is number => typeof bytes === "number");
    const count = ds.presets?.length ?? 0;
    const presets = `${count} ${count === 1 ? "preset" : "presets"}`;
    return {
      label: "Download",
      hint: sizes.length ? `${presets} · from ${formatBytes(Math.min(...sizes))}` : presets,
      color: "var(--ok)",
      dot: "#3fb950",
    };
  }
  if (ds.status === "local-import") {
    // A dataset being prepared on a cluster this session: "Prepared on HPC · 3/33 train".
    const id = ds.id.toLowerCase();
    const prepared = clusterJobs.find((entry) => entry.label.toLowerCase().includes(id));
    return {
      label: "Local import",
      hint: prepared ? `Prepared on HPC · ${prepared.ready}/${prepared.total}${splitOf(prepared.label)}` : "",
      color: "var(--text-secondary)",
      dot: "#8e8e93",
    };
  }
  return { label: "Coming later", hint: "Adapter in preparation", color: "var(--text-subtle)", dot: "#48484a" };
}

function splitOf(label: string): string {
  const split = label.split("·").map((part) => part.trim())[1];
  return split ? ` ${split}` : "";
}

/** The preset's disk need against free space, on a log scale, with a tick at free. */
function DiskFit({ need, free }: { need: number; free: number }) {
  const mb = (bytes: number) => Math.max(1, bytes / 1024 ** 2);
  const top = Math.log10(Math.max(mb(need), mb(free)) * 1.2);
  const width = (Math.log10(mb(need)) / top) * 100;
  const tick = (Math.log10(mb(free)) / top) * 100;
  return (
    <span className="c-diskfit" role="img" aria-label={`Needs ${formatBytes(need)} of ${formatBytes(free)} free`}>
      <span className={`c-diskfit-bar${need > free ? " is-over" : ""}`} style={{ width: `${width}%` }} />
      <span className="c-diskfit-tick" style={{ left: `${tick}%` }} />
    </span>
  );
}

export default function GetDataScreen({
  onOpenScene,
  onOpenImportFolder,
  onOpenLibrary,
  initialDatasetId,
  initialPresetId,
}: Props) {
  const [clusterJobs, setClusterJobs] = useState<readonly DatasetJobs[]>(() => datasetJobs());
  useEffect(() => subscribeDatasetJobs(() => setClusterJobs(datasetJobs())), []);
  const [generatingDemo, setGeneratingDemo] = useState(false);
  const [demoElapsedSec, setDemoElapsedSec] = useState(0);
  const [demoStage, setDemoStage] = useState<string>("");
  const [demoError, setDemoError] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<CatalogDataset[]>([]);
  const [catalogLoading, setCatalogLoading] = useState<boolean>(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [presetBytes, setPresetBytes] = useState<Record<string, number>>({});
  const [presetSizeNote, setPresetSizeNote] = useState<Record<string, string>>({});
  const [presetRuns, setPresetRuns] = useState<Record<string, DryRunResult>>({});
  const [inspectId, setInspectId] = useState<string | null>(null);
  const [libraryRoot, setLibraryRoot] = useState<string>("");
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
            setPresetRuns((prev) => ({ ...prev, [key]: measured }));
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
        presetRuns?: Record<string, DryRunResult>;
      }>;
      if (custom.detail?.catalog !== undefined) {
        setCatalog(custom.detail.catalog);
        setCatalogLoading(false);
        setCatalogError(null);
      }
      if (custom.detail?.presetBytes) {
        setPresetBytes(custom.detail.presetBytes);
      }
      if (custom.detail?.presetRuns) {
        setPresetRuns(custom.detail.presetRuns);
      }
    };
    window.addEventListener("coperceive-test-catalog-seed", handleSeed);
    return () => window.removeEventListener("coperceive-test-catalog-seed", handleSeed);
  }, []);

  useEffect(() => {
    void getStorageInfo()
      .then((stats) => setLibraryRoot(stats?.library_root ?? ""))
      .catch(() => undefined);
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

  const [measuredDemoBytes, setMeasuredDemoBytes] = useState<number | null>(null);

  const handleOpenDemo = async () => {
    setDemoElapsedSec(0);
    setDemoStage("Starting bundled Python generator…");
    setDemoError(null);
    try {
      const outcome = await runDemoOpenFlow({
        ensureDemo: ensureDemoScene,
        subscribeProgress: onDemoProgress,
        onGeneratingStart: () => setGeneratingDemo(true),
        onGeneratingEnd: () => setGeneratingDemo(false),
        onProgress: (stage, elapsed) => {
          if (stage) setDemoStage(stage);
          setDemoElapsedSec(elapsed);
        },
        onDemoBytes: (bytes) => setMeasuredDemoBytes(bytes),
      });
      onOpenScene(outcome.path);
    } catch (err) {
      setGeneratingDemo(false);
      const msg = errorText(err);
      if (!msg.toLowerCase().includes("cancelled")) {
        setDemoError(msg);
      }
    }
  };

  const handleCancelDemo = async () => {
    await cancelDemoScene();
    setGeneratingDemo(false);
  };

  useEffect(() => {
    if (initialDatasetId && catalog.length > 0) {
      const ds = catalog.find((row) => row.id === initialDatasetId);
      if (ds) {
        setInspectId(ds.id);
        void handleSelectPreset(ds, initialPresetId || "dev");
      }
    }
  }, [initialDatasetId, initialPresetId, catalog]);

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

  const readyCount = catalog.filter((ds) => ds.status === "ready").length;
  usePageHeader("get_data", {
    meta: catalog.length
      ? `Cooperative-perception benchmarks · ${readyCount} ready to download`
      : undefined,
    actions: onOpenImportFolder ? (
      <button type="button" className="c-header-action" onClick={() => onOpenImportFolder()}>
        Import local folder…
      </button>
    ) : undefined,
  });
  useDefaultSelection(catalog, datasetKey, inspectId, setInspectId);
  const inspected = catalog.find((ds) => ds.id === inspectId) ?? null;
  const measuredRuns = Object.values(presetRuns);
  const freeBytes = measuredRuns.length ? measuredRuns[measuredRuns.length - 1].free_bytes : null;

  const columns: GridColumn<CatalogDataset>[] = [
    {
      key: "dataset",
      header: "Dataset",
      min: 200,
      max: 420,
      render: (ds) => (
        <span style={{ display: "flex", flexDirection: "column", gap: "2px", minWidth: 0 }}>
          <span style={{ fontWeight: 600, color: "var(--text)" }}>{ds.name}</span>
          <span className="c-ellipsis" style={{ fontSize: "12px", color: "var(--text-subtle)" }}>
            {ds.description}
          </span>
        </span>
      ),
    },
    { key: "agents", header: "Agents", min: 120, max: 200, render: (ds) => ds.agents },
    {
      key: "modalities",
      header: "Modalities",
      min: 120,
      max: 140,
      render: (ds) => (
        <span style={{ display: "flex", gap: "4px" }}>
          {ds.modalities.map((modality) => (
            <span key={modality} className="c-chip">
              {modality === "Camera" ? "Cam" : modality}
            </span>
          ))}
        </span>
      ),
    },
    {
      key: "licence",
      header: "Licence",
      min: 90,
      max: 110,
      render: (ds) => (ds.licence === "unknown" ? <span style={{ color: "var(--text-subtle)" }}>Unknown</span> : ds.licence),
    },
    {
      key: "availability",
      header: "Availability",
      min: 160,
      max: 260,
      render: (ds) => {
        const availability = availabilityOf(ds, presetBytes, clusterJobs);
        return (
          <span style={{ display: "flex", flexDirection: "column", gap: "2px", minWidth: 0 }}>
            <span style={{ display: "flex", alignItems: "center", gap: "6px", color: availability.color }}>
              <span className="c-dot" style={{ background: availability.dot }} />
              {availability.label}
            </span>
            {availability.hint ? (
              <span className="c-ellipsis" style={{ fontSize: "11.5px", color: "var(--text-subtle)" }}>
                {availability.hint}
              </span>
            ) : null}
          </span>
        );
      },
    },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "calc(100vh - 52px)", fontSize: "13px" }}>
      <PageLayout
        inspectorLabel="Selected dataset"
        inspector={
          inspected ? (
            <>
              <InspectorSection>
                <div style={{ fontSize: "15px", fontWeight: 600, color: "var(--text)" }}>{inspected.name}</div>
                <div style={{ color: "var(--text-subtle)", fontSize: "12px" }}>{inspected.description}</div>
              </InspectorSection>
              <InspectorSection>
                <dl className="c-pairs">
                  <dt>Agents</dt>
                  <dd>{inspected.agents}</dd>
                  <dt>Licence</dt>
                  <dd>{inspected.licence === "unknown" ? "Not recorded" : inspected.licence}</dd>
                  {inspected.paper_url || inspected.source_url ? (
                    <>
                      <dt>Links</dt>
                      <dd style={{ display: "flex", gap: "12px" }}>
                        {inspected.paper_url ? (
                          <a href={inspected.paper_url} target="_blank" rel="noreferrer">
                            Paper ↗
                          </a>
                        ) : null}
                        {inspected.source_url ? (
                          <a href={inspected.source_url} target="_blank" rel="noreferrer">
                            Source ↗
                          </a>
                        ) : null}
                      </dd>
                    </>
                  ) : null}
                </dl>
              </InspectorSection>
              {inspected.status === "ready" && inspected.presets?.length ? (
                <InspectorSection
                  title={
                    <span style={{ display: "flex", justifyContent: "space-between" }}>
                      <span>Presets</span>
                      {freeBytes !== null ? <span>{formatBytes(freeBytes)} free</span> : null}
                    </span>
                  }
                >
                  {inspected.presets.map((preset) => {
                    const key = `${inspected.id}:${preset.id}`;
                    const run = presetRuns[key];
                    const bytes = presetBytes[key];
                    return (
                      <div key={preset.id} className="c-preset">
                        <button
                          type="button"
                          className="c-preset-head"
                          onClick={() => void handleSelectPreset(inspected, preset.id)}
                        >
                          <span style={{ fontWeight: 500, color: "var(--text)" }}>{preset.name}</span>
                          <span className="c-mono" style={{ fontSize: "12px" }}>
                            {typeof bytes === "number" ? formatBytes(bytes) : presetSizeNote[key] || ""}
                          </span>
                        </button>
                        <span style={{ fontSize: "12px", color: "var(--text-subtle)" }}>{preset.description}</span>
                        {run ? (
                          <>
                            <DiskFit need={run.disk_bytes} free={run.free_bytes} />
                            <span style={{ fontSize: "11.5px", color: run.has_headroom ? "var(--ok)" : "var(--danger)" }}>
                              {run.has_headroom
                                ? `Fits · ${formatBytes(run.free_bytes - run.disk_bytes)} left after`
                                : `Needs ${formatBytes(run.disk_bytes)} · only ${formatBytes(run.free_bytes)} free`}
                            </span>
                          </>
                        ) : null}
                        {run && !run.has_headroom ? (
                          <button type="button" className="c-secondary" onClick={onOpenLibrary}>
                            Free up space
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="c-secondary"
                            onClick={() => void handleSelectPreset(inspected, preset.id)}
                            aria-label={`Download ${inspected.name}: ${preset.name}`}
                          >
                            Download
                          </button>
                        )}
                      </div>
                    );
                  })}
                  {measuredRuns.length ? (
                    <span style={{ fontSize: "11px", color: "var(--text-subtle)" }}>
                      Bars on a log scale · the tick marks free disk space
                    </span>
                  ) : null}
                </InspectorSection>
              ) : (
                <InspectorSection>
                  <span>
                    {inspected.status === "planned"
                      ? "The adapter for this dataset is in preparation."
                      : "This dataset has no recorded licence, so the app never downloads it. Import a copy you already have, or prepare it on a cluster."}
                  </span>
                  {inspected.status === "local-import" && onOpenImportFolder ? (
                    <button
                      type="button"
                      className="c-secondary"
                      onClick={async () => {
                        const folder = await pickDataRoot();
                        if (folder) onOpenImportFolder(folder);
                      }}
                    >
                      Import a copy…
                    </button>
                  ) : null}
                </InspectorSection>
              )}
            </>
          ) : null
        }
      >
        {resumableJob && !jobRunning && (
          <div className="c-banner">
            <div>
              <strong style={{ color: "var(--text)" }}>
                Resume download of {resumableJob.datasetId} ({resumableJob.presetId})?
              </strong>{" "}
              <span style={{ color: "var(--text-tertiary)" }}>Previous download was interrupted before completion.</span>
            </div>
            <div style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
              <button type="button" className="c-secondary" onClick={() => handleResumeJob(resumableJob)}>
                Resume
              </button>
              <button
                type="button"
                className="c-link"
                onClick={() => {
                  localStorage.removeItem("coperceive_incomplete_download");
                  setResumableJob(null);
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
        {catalogError && (
          <p role="alert" style={{ color: "var(--danger)", margin: "12px 24px", fontSize: "12.5px" }}>
            {catalogError}
          </p>
        )}

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

        {catalog.length > 0 ? (
          <>
            <GridTable
              label="Dataset catalog"
              columns={columns}
              rows={catalog}
              rowKey={datasetKey}
              selectedKey={inspected?.id ?? null}
              onSelect={(ds) => setInspectId(ds.id)}
              rowHeight={52}
              rowClassName={(ds) => (ds.status === "planned" ? "is-muted" : undefined)}
            />
            <div
              className="c-demo-card"
              role="region"
              aria-label="Offline demo"
              style={{
                margin: "12px 24px 0",
                padding: "14px 18px",
                background: "var(--sunken)",
                borderRadius: "8px",
                border: "1px solid var(--line)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: "16px",
              }}
            >
              <div>
                <div style={{ fontWeight: 600, fontSize: "13px", color: "var(--text)" }}>
                  Offline demo scene
                </div>
                <div style={{ fontSize: "12px", color: "var(--text-subtle)", marginTop: "2px" }}>
                  {generatingDemo
                    ? `${demoStage || "Generating demo scene…"} · ${demoElapsedSec}s elapsed · This can take up to 2 minutes on older Macs.`
                    : `Deterministic synthetic cooperative scene with 3 agents (LiDAR & cameras) and simulated predictions (${measuredDemoBytes ? formatDemoSize(measuredDemoBytes) : DEMO_APPROX_SIZE_LABEL} demo cache in app data).`}
                </div>
                {demoError && (
                  <div style={{ fontSize: "11.5px", color: "var(--danger)", marginTop: "4px" }}>
                    Failed to generate demo scene: {demoError}
                  </div>
                )}
              </div>
              <div style={{ display: "flex", gap: "8px", alignItems: "center", flexShrink: 0 }}>
                {generatingDemo && (
                  <button
                    type="button"
                    className="c-secondary"
                    onClick={() => void handleCancelDemo()}
                  >
                    Cancel
                  </button>
                )}
                <button
                  type="button"
                  className="c-primary"
                  disabled={generatingDemo}
                  onClick={() => void handleOpenDemo()}
                >
                  {generatingDemo ? `Generating… ${demoElapsedSec}s` : "Open the demo"}
                </button>
              </div>
            </div>
            <p className="c-page-note">
              {libraryRoot ? `Downloads go to ${libraryRoot}. ` : ""}
              Datasets without a recorded licence can only be imported from a copy you already have — the app never
              downloads them.
            </p>
          </>
        ) : null}
      </PageLayout>

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
                      <span>
                        Stage: <strong>{jobStage}</strong>
                      </span>
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
