// SPDX-License-Identifier: Apache-2.0
/** The HPC workspace: saved servers, dataset view, and evaluation run-plan panels. */
import { useEffect, useState } from "react";
import HpcServers from "./HpcServers";
import NewEvaluation from "./NewEvaluation";
import RunHistory from "./RunHistory";
import ClusterDatasetView from "./ClusterDatasetView";
import type { ServerEntry } from "./hpc";
import type { ClusterDetectDocument } from "./clusterDatasets";

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
      {tab === "servers" ? (
        <HpcServers />
      ) : tab === "dataset" && activeDatasetData ? (
        <ClusterDatasetView
          profile={activeDatasetData.profile}
          dataset={activeDatasetData.dataset}
          onBack={() => setTab("servers")}
          onClose={() => {
            setActiveDatasetData(null);
            setTab("servers");
          }}
        />
      ) : tab === "evaluation" ? (
        <NewEvaluation />
      ) : (
        <RunHistory />
      )}
    </div>
  );
}
