import * as THREE from "three";
import { enableBloom } from "./bloom";
import {
  rowKey,
  type CooperativeView,
  type EvalPick,
  type PredictionRow,
  type RunTag,
} from "./predictionTypes";

/**
 * Prediction wires on the data layer, docs/spec/predictions.md §Colours and styles.
 * Style is the primary cue, colour the second; `below_threshold` shares the `ignored` style.
 */

export const STATUS_COLORS = {
  tp: 0x1fa3a3,
  fp: 0xe0a431,
  miss: 0x8e6cef,
  ignored: 0x6b6b6b,
} as const;

export type StatusChip = keyof typeof STATUS_COLORS;

export const STATUS_CHIPS: readonly StatusChip[] = ["tp", "fp", "miss", "ignored"];

/** The status word each chip and legend row shows; `ignored` also covers `below_threshold`. */
export const STATUS_WORDS: Record<StatusChip, string> = { tp: "TP", fp: "FP", miss: "Miss", ignored: "Ignored" };

export const COOPERATIVE_VIEWS: readonly CooperativeView[] = [
  "recovered",
  "lost_by_fusion",
  "only_other_agent",
  "nobody",
];

/** Dash and gap in metres (null is solid), line width relative to a ground-truth wire, opacity. */
export type LineStyle = { dash: readonly [number, number] | null; width: number; opacity: number };

export const STATUS_STYLES: Record<StatusChip, LineStyle> = {
  tp: { dash: null, width: 1, opacity: 1 },
  fp: { dash: [0.6, 0.3], width: 1, opacity: 1 },
  miss: { dash: [0.15, 0.3], width: 1.5, opacity: 1 },
  ignored: { dash: null, width: 1, opacity: 0.5 },
};

export function chipFor(status: string): StatusChip {
  if (status === "tp" || status === "fp" || status === "miss") {
    return status;
  }
  return "ignored";
}

/** The four named cooperative views a ground-truth row belongs to, §Cooperative views. */
export function cooperativeViews(row: PredictionRow): CooperativeView[] {
  const views: CooperativeView[] = [];
  const single = row.status_single;
  const fused = row.status_fused;
  if (single === "miss" && fused === "tp") {
    views.push("recovered");
  }
  if (single === "tp" && fused === "miss") {
    views.push("lost_by_fusion");
  }
  if (single === "miss" && row.other_agent_tp === true) {
    views.push("only_other_agent");
  }
  const known = [single, fused].filter((status): status is string => typeof status === "string");
  if (known.length > 0 && known.every((status) => status === "miss") && row.other_agent_tp !== true) {
    views.push("nobody");
  }
  return views;
}

export type LayerOptions = {
  showStatuses: ReadonlySet<StatusChip>;
  showSources: ReadonlySet<string>;
  cooperative: ReadonlySet<CooperativeView>;
  minScore: number;
  runTag: RunTag;
  runId: string;
};

export type LayerResult = {
  /** One `LineSegments` per status that has rows. */
  group: THREE.Group;
  /** Invisible pick meshes for the prediction rows drawn; `userData.evalPick` is the `EvalPick`. */
  picks: THREE.Group;
  /** Ground-truth rows with `pair_id` set, by `rowKey`: the frame draws those wires at 40% opacity. */
  dimmedGtKeys: Set<string>;
  /** Rows drawn per chip after the filters. */
  counts: Record<StatusChip, number>;
  /** Rows the filters kept, prediction and ground truth. */
  shown: number;
};

const PICK_MATERIAL = new THREE.MeshBasicMaterial({
  transparent: true,
  opacity: 0,
  depthWrite: false,
  colorWrite: false,
});
PICK_MATERIAL.userData.shared = true;

function emptyCounts(): Record<StatusChip, number> {
  return { tp: 0, fp: 0, miss: 0, ignored: 0 };
}

function materialFor(chip: StatusChip): THREE.LineBasicMaterial {
  const style = STATUS_STYLES[chip];
  const color = STATUS_COLORS[chip];
  if (style.dash) {
    return new THREE.LineDashedMaterial({
      color,
      dashSize: style.dash[0],
      gapSize: style.dash[1],
      linewidth: style.width,
      transparent: style.opacity < 1,
      opacity: style.opacity,
    });
  }
  return new THREE.LineBasicMaterial({
    color,
    linewidth: style.width,
    transparent: style.opacity < 1,
    opacity: style.opacity,
  });
}

function segmentsFor(chip: StatusChip, rows: readonly PredictionRow[], tag: RunTag): THREE.LineSegments {
  let floats = 0;
  for (const row of rows) {
    floats += row.lines.length - (row.lines.length % 6);
  }
  const positions = new Float32Array(floats);
  let cursor = 0;
  for (const row of rows) {
    const usable = row.lines.length - (row.lines.length % 6);
    positions.set(row.lines.slice(0, usable), cursor);
    cursor += usable;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.computeBoundingSphere();
  const segments = new THREE.LineSegments(geometry, materialFor(chip));
  const style = STATUS_STYLES[chip];
  if (style.dash) {
    segments.computeLineDistances();
    if (tag === "B") {
      // Run B: the same colours with the dash pattern shifted by half a period.
      const half = (style.dash[0] + style.dash[1]) / 2;
      const distances = geometry.getAttribute("lineDistance");
      if (distances instanceof THREE.BufferAttribute) {
        for (let index = 0; index < distances.count; index += 1) {
          distances.setX(index, distances.getX(index) + half);
        }
        distances.needsUpdate = true;
      }
    }
  }
  segments.renderOrder = 2;
  segments.frustumCulled = false;
  segments.userData.chip = chip;
  segments.userData.runTag = tag;
  enableBloom(segments);
  return segments;
}

function pickFor(row: PredictionRow, pick: EvalPick): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), PICK_MATERIAL);
  const [length, width, height] = row.size_lwh;
  mesh.scale.set(length ?? 1, width ?? 1, height ?? 1);
  const [x, y, z] = row.center;
  mesh.position.set(x ?? 0, y ?? 0, z ?? 0);
  mesh.quaternion.set(row.pose.qx, row.pose.qy, row.pose.qz, row.pose.qw);
  mesh.userData.evalPick = pick;
  return mesh;
}

/**
 * Builds the wires for one run's rows at one sample. A ground-truth row with `pair_id` set is not
 * drawn here (the frame already draws that wire) and comes back in `dimmedGtKeys`.
 */
export function buildPredictionObjects(rows: readonly PredictionRow[], options: LayerOptions): LayerResult {
  const group = new THREE.Group();
  const picks = new THREE.Group();
  const dimmedGtKeys = new Set<string>();
  const counts = emptyCounts();
  const buckets: Record<StatusChip, PredictionRow[]> = { tp: [], fp: [], miss: [], ignored: [] };
  const gtByPair = new Map<number, PredictionRow>();
  for (const row of rows) {
    if (row.kind === "gt" && row.pair_id !== null) {
      gtByPair.set(row.pair_id, row);
    }
  }
  const cooperativeOk = (row: PredictionRow) =>
    options.cooperative.size === 0 || cooperativeViews(row).some((view) => options.cooperative.has(view));
  let shown = 0;
  for (const row of rows) {
    if (!options.showSources.has(row.source)) {
      continue;
    }
    if (row.kind === "gt") {
      if (!cooperativeOk(row)) {
        continue;
      }
      if (row.pair_id !== null) {
        dimmedGtKeys.add(rowKey(row));
        shown += 1;
        continue;
      }
      const chip = chipFor(row.status);
      if (!options.showStatuses.has(chip)) {
        continue;
      }
      buckets[chip].push(row);
      counts[chip] += 1;
      shown += 1;
      continue;
    }
    if (row.score !== null && row.score < options.minScore) {
      continue;
    }
    const chip = chipFor(row.status);
    if (!options.showStatuses.has(chip)) {
      continue;
    }
    const gt = row.pair_id === null ? null : (gtByPair.get(row.pair_id) ?? null);
    if (options.cooperative.size > 0 && (!gt || !cooperativeOk(gt))) {
      continue;
    }
    buckets[chip].push(row);
    counts[chip] += 1;
    shown += 1;
    picks.add(pickFor(row, { tag: options.runTag, runId: options.runId, row, gt }));
  }
  for (const chip of STATUS_CHIPS) {
    if (buckets[chip].length > 0) {
      group.add(segmentsFor(chip, buckets[chip], options.runTag));
    }
  }
  return { group, picks, dimmedGtKeys, counts, shown };
}

function disposeObject(object: THREE.Object3D): void {
  if (object instanceof THREE.LineSegments || object instanceof THREE.Mesh) {
    object.geometry.dispose();
    const material: unknown = object.material;
    if (material instanceof THREE.Material && material.userData.shared !== true) {
      material.dispose();
    }
  }
}

/**
 * Detaches every child of `group` and frees its GPU resources; the shared pick material is kept.
 * A detached layer group keeps its own children, so adding it back re-uploads them.
 */
export function disposePredictionGroup(group: THREE.Group): void {
  for (const child of [...group.children]) {
    group.remove(child);
    child.traverse(disposeObject);
  }
}
