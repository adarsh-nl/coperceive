// SPDX-License-Identifier: Apache-2.0
/** The HPC workspace: saved servers, dataset view, and evaluation run-plan panels. */
import { useEffect, useState } from "react";
import HpcServers from "./HpcServers";
import NewEvaluation from "./NewEvaluation";
import RunHistory from "./RunHistory";
import ClusterDatasetView from "./ClusterDatasetView";
import type { ServerEntry } from "./hpc";
import { lastClusterDataset, rememberClusterDataset, type ClusterDetectDocument } from "./clusterDatasets";
import { listServerProfiles } from "./local";

type WorkspaceTab = "servers" | "evaluation" | "history" | "dataset";

export default function HpcWorkspace() {
  const [tab, setTab] = useState<WorkspaceTab>(() => {
    if (typeof window !== "undefined") {
      try {
        if (sessionStorage.getItem("coperceive-hpc-tab") === "evaluation") {
          sessionStorage.removeItem("coperceive-hpc-tab");
          return "evaluation";
        }
      } catch {}
      if (window.location.hash.includes("evaluation")) {
        return "evaluation";
      }
    }
    return "servers";
  });

  const [activeDatasetData, setActiveDatasetData] = useState<{
    profile: ServerEntry;
    dataset: ClusterDetectDocument;
  } | null>(null);
  // Sections stay mounted once shown, so an open dataset, its scroll and
  // filters survive switching sections (and the app's own tabs).
  const [visited, setVisited] = useState<ReadonlySet<WorkspaceTab>>(() => new Set([tab]));
  useEffect(() => {
    setVisited((current) => (current.has(tab) ? current : new Set([...current, tab])));
  }, [tab]);

  // Reopen the dataset that was open before the app restarted.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const last = await lastClusterDataset();
        if (!last || cancelled) return;
        const profile = (await listServerProfiles()).find((entry) => entry.id === last.profile_id);
        if (!profile || cancelled) return;
        setActiveDatasetData((current) => current ?? { profile, dataset: last.dataset });
        setTab((current) => (current === "servers" ? "dataset" : current));
      } catch {
        // Nothing to reopen.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handleOpenEval = () => {
      setTab("evaluation");
    };
    const handleOpenDataset = (event: Event) => {
      const customEvent = event as CustomEvent<{
        profile: ServerEntry;
        dataset: ClusterDetectDocument;
      }>;
      if (customEvent.detail) {
        setActiveDatasetData(customEvent.detail);
        setTab("dataset");
        void rememberClusterDataset(customEvent.detail.profile.id, customEvent.detail.dataset).catch(() => undefined);
      }
    };
    window.addEventListener("coperceive-open-evaluation", handleOpenEval);
    window.addEventListener("coperceive-open-dataset", handleOpenDataset);
    return () => {
      window.removeEventListener("coperceive-open-evaluation", handleOpenEval);
      window.removeEventListener("coperceive-open-dataset", handleOpenDataset);
    };
  }, []);
  const tabStyle = (active: boolean) =>
    ({
      padding: "0.45rem 0.9rem",
      borderRadius: "6px",
      border: "none",
      fontSize: "0.85rem",
      fontWeight: active ? 600 : 400,
      cursor: "pointer",
      backgroundColor: active ? "rgba(255, 255, 255, 0.12)" : "transparent",
      color: active ? "#fff" : "#a1a1a6",
    }) as const;
  return (
    <div>
      <div role="tablist" aria-label="HPC workspace sections" style={{ display: "flex", gap: "0.35rem", justifyContent: "center", marginBottom: "0.75rem" }}>
        <button type="button" role="tab" aria-selected={tab === "servers"} style={tabStyle(tab === "servers")} onClick={() => setTab("servers")}>
          Servers
        </button>
        {activeDatasetData ? (
          <button
            type="button"
            role="tab"
            aria-selected={tab === "dataset"}
            style={tabStyle(tab === "dataset")}
            onClick={() => setTab("dataset")}
          >
            Dataset view
          </button>
        ) : null}
        <button type="button" role="tab" aria-selected={tab === "evaluation"} style={tabStyle(tab === "evaluation")} onClick={() => setTab("evaluation")}>
          New evaluation
        </button>
        <button type="button" role="tab" aria-selected={tab === "history"} style={tabStyle(tab === "history")} onClick={() => setTab("history")}>
          Run history
        </button>
      </div>
      <div hidden={tab !== "servers"}>{visited.has("servers") ? <HpcServers /> : null}</div>
      <div hidden={tab !== "dataset"}>
        {activeDatasetData ? (
          <ClusterDatasetView
            key={`${activeDatasetData.profile.id}|${activeDatasetData.dataset.path}`}
            profile={activeDatasetData.profile}
            dataset={activeDatasetData.dataset}
            onBack={() => setTab("servers")}
            onClose={() => {
              void rememberClusterDataset(activeDatasetData.profile.id, null).catch(() => undefined);
              setActiveDatasetData(null);
              setTab("servers");
            }}
          />
        ) : null}
      </div>
      <div hidden={tab !== "evaluation"}>{visited.has("evaluation") ? <NewEvaluation /> : null}</div>
      <div hidden={tab !== "history"}>{visited.has("history") ? <RunHistory /> : null}</div>
    </div>
  );
}
