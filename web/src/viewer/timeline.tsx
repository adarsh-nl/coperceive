import { SPEEDS, formatClock, type Speed } from "./playback";

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

function spark(values: number[]): string {
  if (values.length < 2) {
    return "";
  }
  let max = 1;
  for (const value of values) {
    if (value > max) {
      max = value;
    }
  }
  return values
    .map((value, item) => {
      const x = (item / (values.length - 1)) * 100;
      const y = 18 - (Math.max(0, value) / max) * 16;
      return `${item === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

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

  const ticks: { left: number; label: string }[] = [];
  if (length > 1) {
    const count = 4;
    for (let step = 0; step <= count; step += 1) {
      const at = Math.round((last * step) / count);
      const time = times[at];
      ticks.push({
        left: (at / last) * 100,
        label: typeof time === "number" ? formatClock(time) : "",
      });
    }
  }

  return (
    <section className="timeline" aria-label="Timeline">
      <div className="timeline-tools">
        <button type="button" onClick={onToggle} disabled={disabled}>
          {playing ? "Pause" : "Play"}
        </button>
        <button
          type="button"
          className="ghost"
          disabled={disabled}
          onClick={() => onStep(-1)}
        >
          Step back
        </button>
        <button
          type="button"
          className="ghost"
          disabled={disabled}
          onClick={() => onStep(1)}
        >
          Step forward
        </button>
        <label htmlFor="speed">
          Speed
          <select
            id="speed"
            value={String(speed)}
            onChange={(event) => {
              const next = Number(event.target.value);
              if (SPEEDS.includes(next as Speed)) {
                onSpeed(next as Speed);
              }
            }}
          >
            {SPEEDS.map((item) => (
              <option key={item} value={item}>
                {item}×
              </option>
            ))}
          </select>
        </label>
        <label className="check" htmlFor="loop">
          <input id="loop" type="checkbox" checked={loop} onChange={onLoop} />
          Loop
        </label>
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
        <div className="ticks" aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick.left} style={{ left: `${tick.left}%` }}>
              {tick.label}
            </span>
          ))}
        </div>
        {markers.map((marker) => (
          <span
            key={`${marker.index}-${marker.label}`}
            className="marker"
            style={{ left: `${length > 1 ? (marker.index / last) * 100 : 0}%` }}
            title={marker.label}
          />
        ))}
        <span className="playhead" style={{ left: `${place}%` }}>
          <span className="flag num">{stamp}</span>
        </span>
      </div>
      <div className="sparks" aria-hidden="true">
        {(
          [
            ["Points", points, "var(--vehicle)"],
            ["Speed", speeds, "var(--drone)"],
            ["Boxes", boxes, "var(--accent)"],
          ] as const
        ).map(([label, values, color]) => (
          <div key={label} className="spark">
            <span>{label}</span>
            <svg viewBox="0 0 100 18" preserveAspectRatio="none">
              <path d={spark(values)} stroke={color} />
            </svg>
          </div>
        ))}
      </div>
    </section>
  );
}
