// SPDX-License-Identifier: Apache-2.0
/** The HPC workspace: saved servers, dataset view, and evaluation run-plan panels. */
import { useEffect, useState } from "react";
import HpcServers from "./HpcServers";
import NewEvaluation from "./NewEvaluation";
import RunHistory from "./RunHistory";
import ClusterDatasetView from "./ClusterDatasetView";
import DatasetView, { buildV2xsetMockFixture, type SubmittedJob } from "./hpc/DatasetView";
import type { ServerEntry } from "./hpc";
import {
  lastClusterDataset,
  rememberClusterDataset,
  type ClusterDetectDocument,
  type ClusterIndexDocument,
  type ClusterSceneStatusEntry,
} from "./clusterDatasets";
import { listServerProfiles } from "./local";

export type WorkspaceTab = "servers" | "evaluation" | "history" | "dataset";

export interface ActiveDatasetPayload {
  profile: ServerEntry;
  dataset: ClusterDetectDocument;
  useNewView?: boolean;
  indexDoc?: ClusterIndexDocument | null;
  statuses?: ClusterSceneStatusEntry[];
  submittedJobs?: SubmittedJob[];
  cacheRoot?: string;
  selectedSceneId?: string;
}

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

  const [activeDatasetData, setActiveDatasetData] = useState<ActiveDatasetPayload | null>(null);
  // Sections stay mounted once shown, so an open dataset, its scroll and
  // filters survive switching sections (and the app's own tabs).
  const [visited, setVisited] = useState<ReadonlySet<WorkspaceTab>>(() => new Set([tab]));
  useEffect(() => {
    setVisited((current) => (current.has(tab) ? current : new Set([...current, tab])));
  }, [tab]);

  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent("coperceive-hpc-state", {
        detail: { tab, activeDataset: activeDatasetData },
      }),
    );
  }, [tab, activeDatasetData]);

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
    const handleSelectSection = (event: Event) => {
      const custom = event as CustomEvent<{ tab?: WorkspaceTab }>;
      if (custom.detail?.tab) {
        setTab(custom.detail.tab);
      }
    };
    const handleOpenDataset = (event: Event) => {
      const customEvent = event as CustomEvent<ActiveDatasetPayload & { useMockV2xset?: boolean }>;
      if (customEvent.detail?.useMockV2xset) {
        const mock = buildV2xsetMockFixture();
        setActiveDatasetData({
          profile: mock.profile,
          dataset: mock.dataset,
          useNewView: true,
          indexDoc: mock.indexDoc,
          statuses: mock.statuses,
          submittedJobs: mock.submittedJobs,
          cacheRoot: mock.cacheRoot,
          selectedSceneId: mock.selectedSceneId,
        });
        setTab("dataset");
        return;
      }
      if (customEvent.detail) {
        setActiveDatasetData(customEvent.detail);
        setTab("dataset");
        if (!customEvent.detail.indexDoc) {
          void rememberClusterDataset(
            customEvent.detail.profile.id,
            customEvent.detail.dataset,
          ).catch(() => undefined);
        }
      }
    };
    window.addEventListener("coperceive-open-evaluation", handleOpenEval);
    window.addEventListener("coperceive-hpc-section", handleSelectSection);
    window.addEventListener("coperceive-open-dataset", handleOpenDataset);
    return () => {
      window.removeEventListener("coperceive-open-evaluation", handleOpenEval);
      window.removeEventListener("coperceive-hpc-section", handleSelectSection);
      window.removeEventListener("coperceive-open-dataset", handleOpenDataset);
    };
  }, []);

  const handleCloseDataset = () => {
    if (activeDatasetData) {
      void rememberClusterDataset(activeDatasetData.profile.id, null).catch(() => undefined);
    }
    setActiveDatasetData(null);
    setTab("servers");
  };

  return (
    <div style={{ minHeight: "100%", display: "flex", flexDirection: "column" }}>
      <div hidden={tab !== "servers"}>{visited.has("servers") ? <HpcServers /> : null}</div>
      <div hidden={tab !== "dataset"}>
        {activeDatasetData ? (
          activeDatasetData.useNewView || activeDatasetData.indexDoc ? (
            <DatasetView
              key={`${activeDatasetData.profile.id}|${activeDatasetData.dataset.path}|v2`}
              profile={activeDatasetData.profile}
              dataset={activeDatasetData.dataset}
              onBack={() => setTab("servers")}
              onClose={handleCloseDataset}
              initialIndexDoc={activeDatasetData.indexDoc ?? null}
              initialStatuses={activeDatasetData.statuses}
              initialSubmittedJobs={activeDatasetData.submittedJobs}
              initialCacheRoot={activeDatasetData.cacheRoot}
              initialSelectedSceneId={activeDatasetData.selectedSceneId}
            />
          ) : (
            <ClusterDatasetView
              key={`${activeDatasetData.profile.id}|${activeDatasetData.dataset.path}`}
              profile={activeDatasetData.profile}
              dataset={activeDatasetData.dataset}
              onBack={() => setTab("servers")}
              onClose={handleCloseDataset}
            />
          )
        ) : null}
      </div>
      <div hidden={tab !== "evaluation"}>{visited.has("evaluation") ? <NewEvaluation /> : null}</div>
      <div hidden={tab !== "history"}>{visited.has("history") ? <RunHistory /> : null}</div>
    </div>
  );
}
