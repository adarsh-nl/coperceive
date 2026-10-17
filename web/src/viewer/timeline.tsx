import { IconLoop, IconPlay, IconStep, Menu } from "./controls";
import { SPEEDS, formatClock, type Speed } from "./playback";
import type { EvalSeries } from "./predictionTypes";
import { sparkPaths } from "./telemetry";

export type Marker = { index: number; label: string };

/** A sample is flagged when its recall is below the scene median by more than this. */
const RECALL_DROP = 0.3;

/** Like `sparkPaths`, with a shared ceiling so two runs or two series sit on one scale. NaN draws as 0. */
function scaledPath(values: readonly number[], height: number, max: number): string {
  if (values.length < 2) {
    return "";
  }
  const top = height - 1;
  const ceiling = max > 1e-6 ? max : 1e-6;
  return values
    .map((value, item) => {
      const x = (item / (values.length - 1)) * 100;
      const safe = Number.isFinite(value) ? Math.max(0, value) : 0;
      const y = top - (safe / ceiling) * (top - 1);
      return `${item === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function finiteMax(...lists: readonly (readonly number[])[]): number {
  let max = 0;
  for (const list of lists) {
    for (const value of list) {
      if (Number.isFinite(value) && value > max) {
        max = value;
      }
    }
  }
  return max;
}

/** Contiguous sample ranges where recall is below the scene median by more than RECALL_DROP. */
function recallDropRanges(recall: readonly number[]): { start: number; end: number }[] {
  const known = recall.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (known.length === 0) {
    return [];
  }
  const middle = Math.floor(known.length / 2);
  const low = known[middle - 1];
  const high = known[middle];
  const median = known.length % 2 === 1 ? (high ?? 0) : ((low ?? 0) + (high ?? 0)) / 2;
  const ranges: { start: number; end: number }[] = [];
  recall.forEach((value, index) => {
    if (!Number.isFinite(value) || value >= median - RECALL_DROP) {
      return;
    }
    const last = ranges[ranges.length - 1];
    if (last && last.end === index - 1) {
      last.end = index;
    } else {
      ranges.push({ start: index, end: index });
    }
  });
  return ranges;
}

type TimelineProps = {
  length: number;
  index: number;
  timeUs: number | null;
  times: readonly number[];
  playing: boolean;
  speed: Speed;
  loop: boolean;
  disabled: boolean;
  points: number[];
  speeds: number[];
  boxes: number[];
  markers: Marker[];
  /** Per-sample precision, recall and FP count of the selected output of run A. */
  evalSeries?: EvalSeries | null;
  /** The same for run B in compare mode, drawn thinner. */
  evalCompare?: EvalSeries | null;
  onIndex: (index: number) => void;
  onStep: (delta: number) => void;
  onToggle: () => void;
  onSpeed: (speed: Speed) => void;
  onLoop: () => void;
};

export default function Timeline({
  length,
  index,
  timeUs,
  times,
  playing,
  speed,
  loop,
  disabled,
  points,
  speeds,
  boxes,
  markers,
  evalSeries = null,
  evalCompare = null,
  onIndex,
  onStep,
  onToggle,
  onSpeed,
  onLoop,
}: TimelineProps) {
  const last = Math.max(length - 1, 0);
  const place = length > 1 ? (Math.min(index, last) / last) * 100 : 0;
  const stamp = timeUs === null ? "0.0 s" : formatClock(timeUs);
  const dropRanges = evalSeries ? recallDropRanges(evalSeries.recall) : [];
  const fpMax = evalSeries ? finiteMax(evalSeries.fp, evalCompare?.fp ?? []) : 0;
  const evalHeight = 28;

  function seek(clientX: number, target: HTMLElement) {
    const box = target.getBoundingClientRect();
    if (box.width <= 0 || length < 2) {
      return;
    }
    const ratio = Math.min(1, Math.max(0, (clientX - box.left) / box.width));
    onIndex(Math.round(ratio * last));
  }

  const ticks: { left: number; label: string; edge: "start" | "mid" | "end" }[] = [];
  if (length > 1) {
    const count = 2;
    for (let step = 0; step <= count; step += 1) {
      const at = Math.round((last * step) / count);
      const time = times[at];
      ticks.push({
        left: (at / last) * 100,
        label: typeof time === "number" ? formatClock(time) : "",
        edge: step === 0 ? "start" : step === count ? "end" : "mid",
      });
    }
  }
  const charts = [
    ["Points", points, "#F58A1F"],
    ["Speed", speeds, "#3C8DFF"],
    ["Boxes", boxes, "#6B6B6B"],
  ] as const;

  return (
    <section className="timeline" aria-label="Timeline">
      <div className="timeline-tools">
        <button type="button" className="icon primary" onClick={onToggle} disabled={disabled} aria-label={playing ? "Pause" : "Play"}>
          <IconPlay playing={playing} />
        </button>
        <button
          type="button"
          className="icon ghost"
          disabled={disabled}
          aria-label="Step back"
          onClick={() => onStep(-1)}
        >
          <IconStep direction={-1} />
        </button>
        <button
          type="button"
          className="icon ghost"
          disabled={disabled}
          aria-label="Step forward"
          onClick={() => onStep(1)}
        >
          <IconStep direction={1} />
        </button>
        <Menu
          id="speed"
          label="Speed"
          value={String(speed)}
          options={SPEEDS.map((item) => ({ value: String(item), label: `${item}×` }))}
          onChange={(next) => {
            const value = Number(next);
            if (SPEEDS.includes(value as Speed)) {
              onSpeed(value as Speed);
            }
          }}
        />
        <button type="button" className="icon ghost" aria-pressed={loop} aria-label="Loop" onClick={onLoop}>
          <IconLoop />
        </button>
        <p className="clock num">
          {stamp}
          <span>
            {" "}
            · sample {length === 0 ? 0 : Math.min(index, last) + 1} / {length}
          </span>
        </p>
        <details className="keys">
          <summary>Keys</summary>
          <div className="keys-pop" role="region" aria-label="Keyboard shortcuts">
            <p>
              <kbd>Space</kbd> play or pause
            </p>
            <p>
              <kbd>←</kbd> <kbd>→</kbd> step
            </p>
            <p>
              <kbd>L</kbd> loop
            </p>
            <p>
              <kbd>[</kbd> <kbd>]</kbd> speed
            </p>
            <p>
              <kbd>1</kbd>–<kbd>5</kbd> viewpoints
            </p>
            <p>
              <kbd>3</kbd> toggles chase
            </p>
            <p>
              <kbd>C</kbd> camera strip
            </p>
            <p>
              <kbd>Esc</kbd> closes the split camera
            </p>
          </div>
        </details>
      </div>
      <div className="timeline-chart">
        <div className="ticks" aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick.left} className={`tick ${tick.edge} num`} style={{ left: `${tick.left}%` }}>
              {tick.label}
            </span>
          ))}
        </div>
        <div
          className="track"
          role="slider"
          aria-label="Sample"
          aria-valuemin={0}
          aria-valuemax={last}
          aria-valuenow={Math.min(index, last)}
          aria-valuetext={`${stamp}, sample ${length === 0 ? 0 : Math.min(index, last) + 1} of ${length}`}
          tabIndex={0}
          onPointerDown={(event) => {
            if (event.button !== 0) {
              return;
            }
            const track = event.currentTarget;
            track.setPointerCapture(event.pointerId);
            seek(event.clientX, track);
          }}
          onPointerMove={(event) => {
            if (!event.currentTarget.hasPointerCapture(event.pointerId)) {
              return;
            }
            seek(event.clientX, event.currentTarget);
          }}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "Home" || event.key === "End") {
              event.stopPropagation();
            }
            if (event.key === "ArrowLeft") {
              event.preventDefault();
              onStep(-1);
            } else if (event.key === "ArrowRight") {
              event.preventDefault();
              onStep(1);
            } else if (event.key === "Home") {
              onIndex(0);
            } else if (event.key === "End") {
              onIndex(last);
            }
          }}
        >
          {markers.map((marker) => (
            <span
              key={`${marker.index}-${marker.label}`}
              className="marker"
              style={{ left: `${length > 1 ? (marker.index / last) * 100 : 0}%` }}
              title={marker.label}
            />
          ))}
          {dropRanges.map((range) => (
            <span
              key={`recall-${range.start}`}
              className="marker marker-range"
              style={{
                left: `${length > 1 ? (range.start / last) * 100 : 0}%`,
                width: `${length > 1 ? ((range.end - range.start) / last) * 100 : 0}%`,
              }}
              title={`Recall below the scene median by more than ${RECALL_DROP}`}
            />
          ))}
        </div>
        <div className="sparks" aria-hidden="true">
          {charts.map(([label, values, color]) => {
            const paths = sparkPaths(values, 28);
            const gradient = `spark-${label}`;
            return (
              <div key={label} className="spark">
                <span>{label}</span>
                <svg viewBox="0 0 100 28" preserveAspectRatio="none">
                  <defs>
                    <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={color} stopOpacity="0.38" />
                      <stop offset="100%" stopColor={color} stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  <path d={paths.area} fill={`url(#${gradient})`} />
                  <path d={paths.line} fill="none" stroke={color} />
                </svg>
              </div>
            );
          })}
          {evalSeries ? (
            <div className="spark spark-eval">
              <span>Precision · Recall</span>
              <svg viewBox={`0 0 100 ${evalHeight}`} preserveAspectRatio="none">
                {evalCompare ? (
                  <>
                    <path className="b" d={scaledPath(evalCompare.precision, evalHeight, 1)} fill="none" stroke="var(--eval-tp)" />
                    <path className="b" d={scaledPath(evalCompare.recall, evalHeight, 1)} fill="none" stroke="var(--eval-miss)" />
                  </>
                ) : null}
                <path d={scaledPath(evalSeries.precision, evalHeight, 1)} fill="none" stroke="var(--eval-tp)" />
                <path d={scaledPath(evalSeries.recall, evalHeight, 1)} fill="none" stroke="var(--eval-miss)" />
              </svg>
            </div>
          ) : null}
          {evalSeries ? (
            <div className="spark spark-eval">
              <span>FP</span>
              <svg viewBox={`0 0 100 ${evalHeight}`} preserveAspectRatio="none">
                <defs>
                  <linearGradient id="spark-eval-fp" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--eval-fp)" stopOpacity="0.38" />
                    <stop offset="100%" stopColor="var(--eval-fp)" stopOpacity="0" />
                  </linearGradient>
                </defs>
                <path
                  d={`${scaledPath(evalSeries.fp, evalHeight, fpMax)} L100 ${evalHeight.toFixed(2)} L0 ${evalHeight.toFixed(2)} Z`}
                  fill="url(#spark-eval-fp)"
                />
                {evalCompare ? (
                  <path className="b" d={scaledPath(evalCompare.fp, evalHeight, fpMax)} fill="none" stroke="var(--eval-fp)" />
                ) : null}
                <path d={scaledPath(evalSeries.fp, evalHeight, fpMax)} fill="none" stroke="var(--eval-fp)" />
              </svg>
            </div>
          ) : null}
        </div>
        <span className="playhead" style={{ left: `${place}%` }}>
          <span className={place > 84 ? "flag num end" : "flag num"}>{stamp}</span>
        </span>
      </div>
    </section>
  );
}
