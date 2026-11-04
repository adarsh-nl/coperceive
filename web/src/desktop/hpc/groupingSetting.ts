// SPDX-License-Identifier: Apache-2.0
/**
 * "Group scenes" (Settings → Datasets, and the dataset view's Group control):
 * stored in state.json as `dataset_grouping`, like the other preferences, and
 * applied live in every open view.
 */
import { useEffect, useState } from "react";
import { getPersistentState } from "../local";
import { isDatasetGrouping, type DatasetGrouping } from "./grouping";

/** Fired on window when the setting changes; detail is the new grouping. */
export const DATASET_GROUPING_EVENT = "coperceive-dataset-grouping";

function controller(): { port: number; secret: string } | null {
  if (typeof window === "undefined" || !window.__COPERCEIVE_CTRL_PORT__) return null;
  return { port: window.__COPERCEIVE_CTRL_PORT__, secret: window.__COPERCEIVE_CTRL_SECRET__ || "" };
}

export async function loadDatasetGrouping(): Promise<DatasetGrouping> {
  const saved = await getPersistentState().catch(() => null);
  if (saved && isDatasetGrouping(saved.dataset_grouping)) return saved.dataset_grouping;
  const ctrl = controller();
  if (ctrl) {
    try {
      const response = await fetch(`http://127.0.0.1:${ctrl.port}/state`, {
        headers: { Authorization: `Bearer ${ctrl.secret}` },
      });
      const state = (await response.json()) as { dataset_grouping?: unknown };
      if (isDatasetGrouping(state.dataset_grouping)) return state.dataset_grouping;
    } catch {
      // Fall back to the default below.
    }
  }
  return "none";
}

export function saveDatasetGrouping(grouping: DatasetGrouping): void {
  window.dispatchEvent(new CustomEvent(DATASET_GROUPING_EVENT, { detail: grouping }));
  const ctrl = controller();
  if (!ctrl) return;
  fetch(`http://127.0.0.1:${ctrl.port}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${ctrl.secret}` },
    body: JSON.stringify({ dataset_grouping: grouping }),
  }).catch(() => undefined);
}

/** The saved grouping, kept in step with every change made anywhere in the app. */
export function useDatasetGrouping(): [DatasetGrouping, (grouping: DatasetGrouping) => void] {
  const [grouping, setGrouping] = useState<DatasetGrouping>("none");
  useEffect(() => {
    let active = true;
    let changed = false;
    const onChange = (event: Event) => {
      const next = (event as CustomEvent<unknown>).detail;
      if (isDatasetGrouping(next)) {
        changed = true;
        setGrouping(next);
      }
    };
    window.addEventListener(DATASET_GROUPING_EVENT, onChange);
    void loadDatasetGrouping().then((saved) => {
      // A change made while the saved value loaded wins.
      if (active && !changed) setGrouping(saved);
    });
    return () => {
      active = false;
      window.removeEventListener(DATASET_GROUPING_EVENT, onChange);
    };
  }, []);
  return [grouping, saveDatasetGrouping];
}
