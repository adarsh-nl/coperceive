// SPDX-License-Identifier: Apache-2.0

/** The nearest prepared scene before or after `current` in `order`, skipping the rest. */
export function neighbourScene(
  order: readonly string[],
  ready: ReadonlySet<string>,
  current: string,
  direction: 1 | -1,
): { scene: string | null; skipped: string[] } {
  const skipped: string[] = [];
  let index = order.indexOf(current);
  if (index < 0) {
    return { scene: null, skipped };
  }
  for (index += direction; index >= 0 && index < order.length; index += direction) {
    const candidate = order[index];
    if (candidate === undefined) break;
    if (ready.has(candidate)) {
      return { scene: candidate, skipped };
    }
    skipped.push(candidate);
  }
  return { scene: null, skipped };
}

export interface ClusterDatasetMeta {
  scenes: string[];
  about: { label: string; server: string } | null;
  statuses: Record<string, string>;
}

/**
 * Ignore stale cluster dataset metadata when the loaded scene(s) do not belong
 * to that cluster dataset (for example, after switching from SSH to a local scene).
 */
export function resolveActiveDataset(
  sceneId: string | null | undefined,
  localScenes: readonly string[],
  cluster: ClusterDatasetMeta,
): ClusterDatasetMeta {
  if (cluster.scenes.length === 0 && !cluster.about) {
    return cluster;
  }
  const clusterSet = new Set(cluster.scenes);
  const currentInCluster = Boolean(sceneId && clusterSet.has(sceneId));
  const anyLocalInCluster = localScenes.some((id) => clusterSet.has(id));
  if (currentInCluster || (cluster.scenes.length > 0 && anyLocalInCluster)) {
    return cluster;
  }
  return { scenes: [], about: null, statuses: {} };
}

export interface SavedPlayheadState {
  last_scene_id?: string | null;
  last_sample_index?: number | null;
  last_timestamp_us?: number | null;
  last_timestamp_ns?: number | null;
}

/**
 * Restore the saved sample index only when reopening the exact same scene;
 * opening a different scene always starts at sample 0.
 */
export function initialSampleIndex(
  init: SavedPlayheadState | undefined,
  sceneId: string,
  timesUs: readonly number[],
): number {
  if (!init || timesUs.length === 0) {
    return 0;
  }
  if (!init.last_scene_id || init.last_scene_id !== sceneId) {
    return 0;
  }
  let targetTs = init.last_timestamp_us;
  if (typeof targetTs !== "number" && typeof init.last_timestamp_ns === "number") {
    targetTs = Math.round(init.last_timestamp_ns / 1000);
  }
  if (typeof targetTs === "number") {
    const exact = timesUs.indexOf(targetTs);
    if (exact >= 0) {
      return exact;
    }
    let bestIdx = 0;
    let bestDiff = Math.abs(timesUs[0] - targetTs);
    for (let i = 1; i < timesUs.length; i += 1) {
      const diff = Math.abs(timesUs[i] - targetTs);
      if (diff < bestDiff) {
        bestDiff = diff;
        bestIdx = i;
      }
    }
    return bestIdx;
  }
  if (typeof init.last_sample_index === "number") {
    return Math.max(0, Math.min(init.last_sample_index, timesUs.length - 1));
  }
  return 0;
}

/**
 * Display the camera count in the Cameras fold summary: total cameras when
 * frustum images are off or all enabled, and `enabled/total` when filtered.
 */
export function camerasFoldCount(
  appearance: { frustumImages: boolean; frustumImageCameras: readonly string[] | null },
  cameraKeys: readonly string[],
): string {
  const total = cameraKeys.length;
  if (!appearance.frustumImages || total === 0 || appearance.frustumImageCameras === null) {
    return String(total);
  }
  const enabledSet = new Set(appearance.frustumImageCameras);
  const enabled = cameraKeys.filter((key) => enabledSet.has(key)).length;
  return enabled === total ? String(total) : `${enabled}/${total}`;
}
