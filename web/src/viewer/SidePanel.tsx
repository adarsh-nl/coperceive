import { useEffect, useState } from "react";
import { classColor } from "./appearance";
import type { FrameCache } from "./cache";
import {
  agentDistances,
  boxCenter,
  classCounts,
  followPick,
  matchBox,
  sceneDuration,
  visibleAgents,
  type PickRecord,
} from "./inspect";
import { formatMetres, formatSpeed } from "./telemetry";
import type { FrameDocument, SceneSummary } from "./types";
import { isPose } from "./types";

function cssColor(value: number): string {
  return `#${value.toString(16).padStart(6, "0")}`;
}

export function SceneCard({ summary, frame }: { summary: SceneSummary | null; frame: FrameDocument | null }) {
  const duration = summary ? sceneDuration(summary) : null;
  const counts = classCounts(frame?.boxes ?? []);
  const peak = counts.reduce((max, row) => Math.max(max, row.count), 1);
  return (
    <section className="side-card" aria-label="Scene summary">
      <h3>Scene</h3>
      <dl className="stat-grid">
        <div>
          <dt>Duration</dt>
          <dd className="num">{duration === null ? "—" : `${duration.toFixed(1)} s`}</dd>
        </div>
        <div>
          <dt>Samples</dt>
          <dd className="num">{summary?.times_us.length ?? 0}</dd>
        </div>
        <div>
          <dt>Agents</dt>
          <dd className="num">{summary?.agents.length ?? 0}</dd>
        </div>
        <div>
          <dt>Boxes</dt>
          <dd className="num">{frame?.boxes.length ?? 0}</dd>
        </div>
      </dl>
      {counts.length === 0 ? <p className="muted">No boxes in this sample.</p> : null}
      <ul className="bars">
        {counts.map((row) => (
          <li key={row.name}>
            <span>{row.name}</span>
            <span className="bar" aria-hidden="true">
              <i style={{ width: `${(row.count / peak) * 100}%`, background: cssColor(classColor(row.name)) }} />
            </span>
            <span className="num">{row.count}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Inspector({
  pick,
  frame,
  summary,
  token,
  cache,
}: {
  pick: PickRecord | null;
  frame: FrameDocument | null;
  summary: SceneSummary | null;
  token: string;
  cache: FrameCache;
}) {
  const current = frame && pick ? followPick(frame, pick) : null;
  const [objectSpeed, setObjectSpeed] = useState<number | null>(null);
  useEffect(() => {
    if (!summary || !frame || !pick) {
      setObjectSpeed(null);
      return;
    }
    const live = followPick(frame, pick);
    if (!live) {
      setObjectSpeed(null);
      return;
    }
    const index = summary.times_us.indexOf(frame.time_us);
    const previous = index > 0 ? summary.times_us[index - 1] : summary.times_us[1];
    if (typeof previous !== "number") {
      setObjectSpeed(null);
      return;
    }
    let cancelled = false;
    void cache
      .loadFrame(summary.scene_id, previous, token)
      .then((other) => {
        if (cancelled) {
          return;
        }
        const matched = matchBox(other.boxes, live);
        const dt = Math.abs(frame.time_us - previous) / 1_000_000;
        setObjectSpeed(matched && dt > 0 ? Math.hypot(
          matched[0] - live.center[0],
          matched[1] - live.center[1],
          matched[2] - live.center[2],
        ) / dt : null);
      })
      .catch(() => {
        if (!cancelled) {
          setObjectSpeed(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [cache, frame, pick, summary, token]);

  return (
    <section className="side-card" aria-label="Selected object" aria-live="polite">
      <h3>Selected</h3>
      {!current || !frame ? <p className="muted">Click a box in the view.</p> : null}
      {current && frame ? (
        <>
          <dl className="stat-grid">
            <div className="span-2">
              <dt>Class</dt>
              <dd>{current.category}</dd>
            </div>
            <div className="span-2">
              <dt>Id</dt>
              <dd className="num">{current.trackId ?? current.agentId}</dd>
            </div>
            <div className="span-2">
              <dt>Speed</dt>
              <dd className="num">{formatSpeed(objectSpeed)}</dd>
            </div>
            <div className="span-2">
              <dt>Seen by</dt>
              <dd>{visibleAgents(current, frame.boxes).join(", ")}</dd>
            </div>
          </dl>
          <ul className="distance-list">
            {agentDistances(current, frame).map((row) => (
              <li key={row.agentId}>
                <span>{row.agentId}</span>
                <span className="num">{row.metres === null ? "—" : formatMetres(row.metres)}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

export function Radar({
  frame,
  pick,
  types,
}: {
  frame: FrameDocument | null;
  pick: PickRecord | null;
  types: Map<string, string>;
}) {
  const vehicle = frame?.agents.find((agent) => types.get(agent.agent_id) === "vehicle" && isPose(agent.pose))
    ?? frame?.agents.find((agent) => isPose(agent.pose));
  const origin = isPose(vehicle?.pose) ? vehicle.pose : null;
  const current = frame && pick ? followPick(frame, pick) : null;
  const range = 80;
  const size = 200;
  const half = size / 2;
  function place(x: number, y: number): [number, number] | null {
    if (!origin) {
      return null;
    }
    const sx = half + ((x - origin.tx) / range) * (half - 10);
    const sy = half - ((y - origin.ty) / range) * (half - 10);
    if (sx < 4 || sy < 4 || sx > size - 4 || sy > size - 4) {
      return null;
    }
    return [sx, sy];
  }
  return (
    <section className="side-card radar-card" aria-label="Bird's-eye radar, 80 metres">
      <h3>Radar</h3>
      <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Agents and boxes within 80 metres, north up">
        <rect width={size} height={size} rx="8" fill="#141414" />
        {[20, 40, 60, 80].map((ring) => (
          <circle key={ring} cx={half} cy={half} r={((ring / range) * (half - 10))} fill="none" stroke="#2e2e2e" />
        ))}
        <path d={`M${half} 12 V${size - 12} M12 ${half} H${size - 12}`} stroke="#2a2a2a" />
        <text x={half + 4} y="18" fill="#a3a3a3" fontSize="9">N</text>
        {(frame?.boxes ?? []).map((box, index) => {
          const center = boxCenter(box);
          if (!center) {
            return null;
          }
          const point = place(center[0], center[1]);
          if (!point) {
            return null;
          }
          const selected = current?.index === index && current.agentId === box.agent_id;
          return <circle key={`${box.agent_id}-${index}`} cx={point[0]} cy={point[1]} r={selected ? 3.5 : 1.6} fill={selected ? "#f4f4ed" : "#8a8a8a"} />;
        })}
        {(frame?.agents ?? []).map((agent) => {
          if (!isPose(agent.pose)) {
            return null;
          }
          const point = place(agent.pose.tx, agent.pose.ty);
          if (!point) {
            return null;
          }
          const drone = types.get(agent.agent_id) === "uav";
          return <circle key={agent.agent_id} cx={point[0]} cy={point[1]} r="4" fill={drone ? "#3C8DFF" : "#F58A1F"} />;
        })}
      </svg>
      <p className="muted">80 m around the first posed agent. North is up.</p>
    </section>
  );
}
