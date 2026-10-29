import { Menu, Slider } from "./controls";
import PanelState from "../desktop/PanelState";
import { PANEL_DEFINITIONS } from "../desktop/panelStates";
import { COOPERATIVE_VIEWS, STATUS_CHIPS, STATUS_STYLES, STATUS_WORDS, type StatusChip } from "./predictionLayer";
import { outputKey, type CooperativeCounts, type CooperativeView, type RunInfo, type RunTag } from "./predictionTypes";

const COOPERATIVE_WORDS: Record<CooperativeView, string> = {
  recovered: "Recovered",
  lost_by_fusion: "Lost by fusion",
  only_other_agent: "Only other agent",
  nobody: "Nobody",
};

type PredictionsBlockProps = {
  /** null while the run list loads; [] when the scene has none. */
  runs: RunInfo[] | null;
  /** Shown when there are no runs, or when the frame could not be read. */
  note: string;
  runId: string;
  onRun: (runId: string) => void;
  compareId: string;
  onCompare: (runId: string) => void;
  /** `agent_id|source` of the output the card and the sparklines follow. */
  outputKey: string;
  onOutput: (key: string) => void;
  sources: readonly string[];
  hiddenSources: ReadonlySet<string>;
  onToggleSource: (source: string) => void;
  hiddenStatuses: ReadonlySet<StatusChip>;
  onToggleStatus: (chip: StatusChip) => void;
  cooperative: ReadonlySet<CooperativeView>;
  onToggleCooperative: (view: CooperativeView) => void;
  /** Rows drawn per status at this sample after the filters; null before the first frame. */
  counts: Record<StatusChip, number> | null;
  /** Scene counts of the selected output, from `metrics.json`. */
  cooperativeCounts: CooperativeCounts | null;
  /** The score the slider is at, and the run's `score_threshold` it starts from. */
  score: number;
  scoreDefault: number | null;
  onScore: (value: number | null) => void;
};

/** A 24 px line in a status style, the same dash and width ratios as the data layer. */
export function LineSample({ chip, tag = "A" }: { chip: StatusChip; tag?: RunTag }) {
  const style = STATUS_STYLES[chip];
  const scale = 10;
  const dash = style.dash ? `${style.dash[0] * scale} ${style.dash[1] * scale}` : undefined;
  const offset = tag === "B" && style.dash ? ((style.dash[0] + style.dash[1]) * scale) / 2 : 0;
  return (
    <svg className="line-sample" width="24" height="6" viewBox="0 0 24 6" aria-hidden="true">
      <line
        x1="0"
        y1="3"
        x2="24"
        y2="3"
        stroke={`var(--eval-${chip})`}
        strokeWidth={1.5 * style.width}
        strokeDasharray={dash}
        strokeDashoffset={offset}
        opacity={style.opacity}
      />
    </svg>
  );
}

function Chip({
  on,
  label,
  count,
  tone,
  onClick,
}: {
  on: boolean;
  label: string;
  count?: number | null;
  tone?: string;
  onClick: () => void;
}) {
  return (
    <button type="button" className={tone ? `chip chip-${tone}` : "chip"} aria-pressed={on} onClick={onClick}>
      {label}
      {typeof count === "number" ? <span className="num">{count}</span> : null}
    </button>
  );
}

export default function PredictionsBlock({
  runs,
  note,
  runId,
  onRun,
  compareId,
  onCompare,
  outputKey: chosenOutput,
  onOutput,
  sources,
  hiddenSources,
  onToggleSource,
  hiddenStatuses,
  onToggleStatus,
  cooperative,
  onToggleCooperative,
  counts,
  cooperativeCounts,
  score,
  scoreDefault,
  onScore,
}: PredictionsBlockProps) {
  const run = runs?.find((item) => item.run_id === runId) ?? null;
  const others = (runs ?? []).filter((item) => item.run_id !== runId && item.evaluated);
  const runLabel = (item: RunInfo) => (item.evaluated ? item.run_id : `${item.run_id} (not evaluated)`);
  return (
    <details open>
      <summary>Predictions</summary>
      {runs === null ? (
        <PanelState
          compact
          kind="loading"
          title={PANEL_DEFINITIONS.predictions.loading.title}
          description={PANEL_DEFINITIONS.predictions.loading.description}
        />
      ) : null}
      {runs !== null && runs.length === 0 ? (
        <PanelState
          compact
          kind="empty"
          title={PANEL_DEFINITIONS.predictions.empty.title}
          description={PANEL_DEFINITIONS.predictions.empty.description}
          actionLabel={PANEL_DEFINITIONS.predictions.empty.actionLabel}
          onAction={() => {
            window.location.hash = "#/connect";
          }}
        />
      ) : null}
      {runs !== null && runs.length > 0 ? (
        <div className="stack">
          <Menu
            id="prediction-run"
            label="Run"
            value={runId}
            options={runs.map((item) => ({ value: item.run_id, label: runLabel(item) }))}
            onChange={onRun}
          />
          {run && !run.evaluated ? (
            <PanelState
              compact
              kind="empty"
              icon="ℹ️"
              title={PANEL_DEFINITIONS.predictions.error.title}
              description={PANEL_DEFINITIONS.predictions.error.description}
            />
          ) : null}
          {run?.evaluated ? (
            <>
              <Menu
                id="prediction-compare"
                label="Compare"
                value={others.some((item) => item.run_id === compareId) ? compareId : ""}
                options={[{ value: "", label: "None" }, ...others.map((item) => ({ value: item.run_id, label: `B: ${item.run_id}` }))]}
                onChange={onCompare}
              />
              <Menu
                id="prediction-output"
                label="Output"
                value={chosenOutput}
                options={run.outputs.map((output) => ({
                  value: outputKey(output.agent_id, output.source),
                  label: `${output.agent_id} · ${output.source}`,
                }))}
                onChange={onOutput}
              />
              <div className="chip-row" role="group" aria-label="Sources">
                {sources.map((source) => (
                  <Chip key={source} on={!hiddenSources.has(source)} label={source} onClick={() => onToggleSource(source)} />
                ))}
              </div>
              <div className="chip-row" role="group" aria-label="Statuses">
                {STATUS_CHIPS.map((chip) => (
                  <Chip
                    key={chip}
                    on={!hiddenStatuses.has(chip)}
                    label={STATUS_WORDS[chip]}
                    count={counts ? counts[chip] : null}
                    tone={chip}
                    onClick={() => onToggleStatus(chip)}
                  />
                ))}
              </div>
              <div className="chip-row" role="group" aria-label="Cooperative views">
                {COOPERATIVE_VIEWS.map((view) => (
                  <Chip
                    key={view}
                    on={cooperative.has(view)}
                    label={COOPERATIVE_WORDS[view]}
                    count={cooperativeCounts ? cooperativeCounts[view] : null}
                    onClick={() => onToggleCooperative(view)}
                  />
                ))}
              </div>
              <Slider label={`Score ≥ ${score.toFixed(2)}`} value={score} min={0} max={1} step={0.01} onChange={onScore} />
              {scoreDefault !== null && Math.abs(score - scoreDefault) > 1e-9 ? (
                <button type="button" className="ghost" onClick={() => onScore(null)}>
                  Back to the run's {scoreDefault.toFixed(2)}
                </button>
              ) : null}
              {note ? <p className="muted">{note}</p> : null}
              <ul className="eval-legend" aria-label="Status legend">
                {STATUS_CHIPS.map((chip) => (
                  <li key={chip}>
                    <LineSample chip={chip} />
                    <span>{STATUS_WORDS[chip]}</span>
                    {compareId ? <LineSample chip={chip} tag="B" /> : null}
                    {compareId ? <span className="muted">B</span> : null}
                  </li>
                ))}
              </ul>
              <p className="muted eval-note">
                {run.run.model} · score: {run.run.score_note}
              </p>
            </>
          ) : null}
        </div>
      ) : null}
    </details>
  );
}
