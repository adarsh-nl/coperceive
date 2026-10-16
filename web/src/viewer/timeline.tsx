import { IconLoop, IconPlay, IconStep, Menu } from "./controls";
import { SPEEDS, formatClock, type Speed } from "./playback";
import { sparkPaths } from "./telemetry";

export type Marker = { index: number; label: string };

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
  onIndex,
  onStep,
  onToggle,
  onSpeed,
  onLoop,
}: TimelineProps) {
  const last = Math.max(length - 1, 0);
  const place = length > 1 ? (Math.min(index, last) / last) * 100 : 0;
  const stamp = timeUs === null ? "0.0 s" : formatClock(timeUs);

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
        </div>
        <span className="playhead" style={{ left: `${place}%` }}>
          <span className={place > 84 ? "flag num end" : "flag num"}>{stamp}</span>
        </span>
      </div>
    </section>
  );
}
