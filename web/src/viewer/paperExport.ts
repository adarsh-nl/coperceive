export const PAPER_METRICS = [
  { id: "precision", label: "Precision" },
  { id: "recall", label: "Recall" },
  { id: "ap", label: "Average precision" },
] as const;

export type PaperMetricId = (typeof PAPER_METRICS)[number]["id"];

export type PaperOptions = {
  decimals: number;
  boldBest: boolean;
  includeCounts: boolean;
  metrics: PaperMetricId[];
  qualitative: boolean;
  allowGriffin: boolean;
  allowCarla: boolean;
};

export type PaperFrame = {
  scene_id: string;
  time_us: number;
  agent_id?: string;
  sensor_id?: string;
};

export type PaperRequest = {
  run_id: string | null;
  decimals: number;
  bold_best: boolean;
  include_counts: boolean;
  metrics: string[];
  qualitative: boolean;
  allow_datasets: string[];
  frames: PaperFrame[];
};

export function defaultPaperOptions(): PaperOptions {
  return {
    decimals: 3,
    boldBest: true,
    includeCounts: true,
    metrics: ["precision", "recall", "ap"],
    qualitative: false,
    allowGriffin: false,
    allowCarla: false,
  };
}

export function clampDecimals(value: number): number {
  if (!Number.isInteger(value)) {
    return 3;
  }
  if (value < 0) {
    return 0;
  }
  if (value > 6) {
    return 6;
  }
  return value;
}

export function toggleMetric(options: PaperOptions, metric: PaperMetricId): PaperOptions {
  const has = options.metrics.includes(metric);
  const metrics = has
    ? options.metrics.filter((item) => item !== metric)
    : [...options.metrics, metric];
  const order = PAPER_METRICS.map((item) => item.id);
  metrics.sort((left, right) => order.indexOf(left) - order.indexOf(right));
  return { ...options, metrics };
}

export function paperRequestBody(
  options: PaperOptions,
  runId: string | null,
  frame: PaperFrame | null,
): PaperRequest {
  const allowDatasets: string[] = [];
  if (options.allowGriffin) {
    allowDatasets.push("griffin");
  }
  if (options.allowCarla) {
    allowDatasets.push("carla");
  }
  return {
    run_id: runId,
    decimals: clampDecimals(options.decimals),
    bold_best: options.boldBest,
    include_counts: options.includeCounts,
    metrics: [...options.metrics],
    qualitative: options.qualitative,
    allow_datasets: allowDatasets,
    frames: options.qualitative && frame ? [frame] : [],
  };
}

export function paperSavedNote(fileName: string): string {
  return `Ready for Overleaf. Saved to ${fileName}.`;
}
