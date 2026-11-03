// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState, useSyncExternalStore } from "react";

import { listenTauri } from "./clusterDatasets";
import { datasetJobs, jobsSummary, setViewerOpen, subscribeDatasetJobs } from "./jobsStore";
import { listServerProfiles } from "./local";

/**
 * Always visible: the cluster datasets with prepare jobs in flight
 * ("15 ready · 3 preparing · 15 queued", job ids, whether the viewer is open).
 * A row opens its dataset; a finished dataset is announced once.
 */
export function JobsIndicator({ onOpenWorkspace }: { onOpenWorkspace: () => void }) {
  const entries = useSyncExternalStore(subscribeDatasetJobs, datasetJobs, datasetJobs);
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    const onFinished = (event: Event) => {
      const detail = (event as CustomEvent<{ label: string; ready: number }>).detail;
      setToast(`${detail.label}: preparation finished, ${detail.ready} scenes ready.`);
    };
    window.addEventListener("coperceive-dataset-finished", onFinished);
    let unlisten: (() => void) | undefined;
    void listenTauri<null>("viewer-closed", () => setViewerOpen(false)).then((stop) => {
      unlisten = stop;
    });
    return () => {
      window.removeEventListener("coperceive-dataset-finished", onFinished);
      unlisten?.();
    };
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 8000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const openDataset = async (key: string) => {
    setOpen(false);
    const entry = entries.find((item) => item.key === key);
    if (!entry) return;
    const profile = (await listServerProfiles().catch(() => [])).find((item) => item.id === entry.profileId);
    onOpenWorkspace();
    if (profile && entry.dataset) {
      window.dispatchEvent(new CustomEvent("coperceive-open-dataset", { detail: { profile, dataset: entry.dataset } }));
    }
  };

  return (
    <div className="jobs-indicator">
      <button
        type="button"
        className="ghost"
        aria-expanded={open}
        title="Preparation jobs on your clusters"
        onClick={() => setOpen((value) => !value)}
      >
        {entries.length > 0 ? `Jobs · ${entries.length} in progress` : "Jobs · none running"}
      </button>
      {open ? (
        <div className="jobs-pop" role="dialog" aria-label="Preparation jobs">
          {entries.length === 0 ? (
            <p className="setting-note">No preparation job is running.</p>
          ) : (
            <ul>
              {entries.map((entry) => (
                <li key={entry.key}>
                  <button type="button" className="ghost" onClick={() => void openDataset(entry.key)}>
                    <span className="jobs-title">{entry.label}</span>
                    <span className="setting-note">
                      {jobsSummary(entry)} · job {entry.jobIds.join(", ") || "done"} · viewer {entry.viewerOpen ? "open" : "closed"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
      {toast ? (
        <div className="app-toast" role="status">
          <span>{toast}</span>
          <button type="button" className="icon ghost" aria-label="Dismiss" onClick={() => setToast(null)}>
            ×
          </button>
        </div>
      ) : null}
    </div>
  );
}
