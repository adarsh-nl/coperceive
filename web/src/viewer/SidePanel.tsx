import { useEffect, useState } from "react";
import PanelState from "../desktop/PanelState";
import { PANEL_DEFINITIONS } from "../desktop/panelStates";
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
import type { EvalPick, EvalSummary, GtEvalDetail } from "./predictionTypes";
import { formatMetres } from "./telemetry";
import type { FrameDocument, SceneSummary } from "./types";
import { agentLabel, isPose } from "./types";

function cssColor(value: number): string {
  return `#${value.toString(16).padStart(6, "0")}`;
}

function ratio(value: number | null): string {
  return value === null ? "—" : value.toFixed(2);
}

function yesNo(value: boolean | null): string {
  return value === null ? "—" : value ? "yes" : "no";
}

/** `b − a` beside a run A figure in compare mode; `higherIsBetter` colours the sign. */
function Delta({ a, b, higherIsBetter = true, digits = 2 }: { a: number | null; b: number | null; higherIsBetter?: boolean; digits?: number }) {
  if (a === null || b === null) {
    return b === null ? null : <span className="delta">B {b.toFixed(digits)}</span>;
  }
  const gap = b - a;
  const tone = Math.abs(gap) < 0.5 * 10 ** -digits ? "" : (gap > 0) === higherIsBetter ? " up" : " down";
  return (
    <span className={`delta num${tone}`} title="Run B minus run A">
      {gap >= 0 ? "+" : "−"}
      {Math.abs(gap).toFixed(digits)}
    </span>
  );
}

function EvalFiguresGrid({ summary }: { summary: EvalSummary }) {
  const { a, b } = summary;
  const views = [
    ["recovered", "Recovered"],
    ["lost_by_fusion", "Lost by fusion"],
    ["only_other_agent", "Only other agent"],
    ["nobody", "Nobody"],
  ] as const;
  return (
    <>
      <h3 className="eval-head">
        Evaluation · {summary.agentId} {summary.source}
        {b ? (
          <span className="tag">A {a.runId} / B {b.runId}</span>
        ) : (
          <span className="tag">{a.runId === "demo" ? "synthetic predictions" : a.runId}</span>
        )}
      </h3>
      <dl className="stat-grid eval-grid">
        {a.map.map((row, index) => (
          <div key={row.threshold}>
            <dt>mAP@{row.threshold}</dt>
            <dd className="num">
              {ratio(row.value)}
              {b ? <Delta a={row.value} b={b.map[index]?.value ?? null} /> : null}
            </dd>
          </div>
        ))}
        <div>
          <dt>Recall</dt>
          <dd className="num">
            {ratio(a.recall)}
            {b ? <Delta a={a.recall} b={b.recall} /> : null}
          </dd>
        </div>
        <div>
          <dt>Precision</dt>
          <dd className="num">
            {ratio(a.precision)}
            {b ? <Delta a={a.precision} b={b.precision} /> : null}
          </dd>
        </div>
        {views.map(([key, label]) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd className="num">
              {a.cooperative[key]}
              {b ? <Delta a={a.cooperative[key]} b={b.cooperative[key]} higherIsBetter={key === "recovered"} digits={0} /> : null}
            </dd>
          </div>
        ))}
        <div className="span-2">
          <dt>Recovery rate</dt>
          <dd className="num">
            {ratio(a.recoveryRate)}
            {b ? <Delta a={a.recoveryRate} b={b.recoveryRate} /> : null}
          </dd>
        </div>
      </dl>
    </>
  );
}

export function SceneCard({
  summary,
  frame,
  evalSummary = null,
}: {
  summary: SceneSummary | null;
  frame: FrameDocument | null;
  /** mAP, recall and the cooperative counts of the selected prediction output, with run B deltas. */
  evalSummary?: EvalSummary | null;
}) {
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
          <dt>Boxes now</dt>
          <dd className="num">{frame?.boxes.length ?? 0}</dd>
        </div>
      </dl>
      {evalSummary ? <EvalFiguresGrid summary={evalSummary} /> : null}
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

function PredictionInspector({ pick, frame }: { pick: EvalPick; frame: FrameDocument | null }) {
  const row = pick.row;
  const gt = pick.gt;
  const asPick: PickRecord = {
    agentId: row.agent_id,
    category: row.category,
    trackId: row.track_id,
    center: [row.center[0] ?? 0, row.center[1] ?? 0, row.center[2] ?? 0],
    size: [row.size_lwh[0] ?? 1, row.size_lwh[1] ?? 1, row.size_lwh[2] ?? 1],
    index: -1,
  };
  return (
    <>
      <dl className="stat-grid">
        <div className="span-2">
          <dt>Run</dt>
          <dd>
            {pick.runId}
            <span className="tag">{pick.tag}</span>
          </dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>
            {row.agent_id} · {row.source}
          </dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd className={`eval-status-${row.status}`}>{row.status === "below_threshold" ? "below threshold" : row.status}</dd>
        </div>
        <div>
          <dt>Class</dt>
          <dd>{row.category}</dd>
        </div>
        <div>
          <dt>Score</dt>
          <dd className="num">{ratio(row.score)}</dd>
        </div>
        <div>
          <dt>Matched GT</dt>
          <dd className="num">{gt ? (gt.track_id ?? `row ${gt.row ?? "?"}`) : "—"}</dd>
        </div>
        <div>
          <dt>IoU</dt>
          <dd className="num">{ratio(row.iou_bev)}</dd>
        </div>
        <div>
          <dt>Centre error</dt>
          <dd className="num">{row.centre_error_m === null ? "—" : formatMetres(row.centre_error_m)}</dd>
        </div>
        <div>
          <dt>Yaw error</dt>
          <dd className="num">{row.yaw_error_deg === null ? "—" : `${row.yaw_error_deg.toFixed(1)}°`}</dd>
        </div>
        {gt ? (
          <>
            <div>
              <dt>GT single</dt>
              <dd>{gt.status_single ?? "—"}</dd>
            </div>
            <div>
              <dt>GT fused</dt>
              <dd>{gt.status_fused ?? "—"}</dd>
            </div>
            <div>
              <dt>Seen by other GT</dt>
              <dd>
                {yesNo(gt.seen_by_other_gt)}
                {gt.other_kind ? ` (${gt.other_kind})` : ""}
              </dd>
            </div>
            <div>
              <dt>Other agent TP</dt>
              <dd>{yesNo(gt.other_agent_tp)}</dd>
            </div>
          </>
        ) : null}
      </dl>
      {frame ? (
        <ul className="distance-list">
          {agentDistances(asPick, frame).map((item) => (
            <li key={item.agentId}>
              <span>{item.agentId}</span>
              <span className="num">{item.metres === null ? "—" : formatMetres(item.metres)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

function GtEvalRows({ detail }: { detail: GtEvalDetail }) {
  return (
    <>
      <ul className="eval-rows" aria-label="Status per output">
        {detail.statuses.map((row) => (
          <li key={`${row.tag}-${row.source}`}>
            <span>
              {row.source}
              <span className="tag">{row.tag}</span>
            </span>
            <span className={`num eval-status-${row.status}`}>
              {row.status}
              {row.score === null ? "" : ` · ${row.score.toFixed(2)}`}
            </span>
          </li>
        ))}
      </ul>
      <dl className="stat-grid">
        <div>
          <dt>Seen by other GT</dt>
          <dd>
            {yesNo(detail.seenByOtherGt)}
            {detail.otherKind ? ` (${detail.otherKind})` : ""}
          </dd>
        </div>
        <div>
          <dt>Other agent TP</dt>
          <dd>{yesNo(detail.otherAgentTp)}</dd>
        </div>
      </dl>
    </>
  );
}

/** "16.1 km/h"; the inspector's speed, in the unit people drive by. */
function formatKmh(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "—" : `${(value * 3.6).toFixed(1)} km/h`;
}

/**
 * Distance from the selected object to each agent with a pose, as a bar. Rows
 * of agents that did not see the object (no box of theirs near it) are faded.
 */
function DistanceRows({
  current,
  frame,
  summary,
}: {
  current: PickRecord;
  frame: FrameDocument;
  summary: SceneSummary | null;
}) {
  const rows = agentDistances(current, frame);
  const seen = new Set(visibleAgents(current, frame.boxes));
  const posed = rows.filter((row) => row.metres !== null);
  const far = posed.reduce((max, row) => Math.max(max, row.metres ?? 0), 1);
  return (
    <>
      <p className="dist-head">
        Distance · seen by {rows.filter((row) => seen.has(row.agentId)).length} of {rows.length}
      </p>
      <ul className="distance-list">
        {rows.map((row) => (
          <li key={row.agentId} className={seen.has(row.agentId) ? undefined : "unseen"}>
            <span title={row.agentId}>{agentLabel(summary, row.agentId)}</span>
            <span className="dist-bar" aria-hidden="true">
              <i style={{ width: `${row.metres === null ? 0 : Math.max(3, (row.metres / far) * 100)}%` }} />
            </span>
            <span className="num">{row.metres === null ? "—" : formatMetres(row.metres)}</span>
          </li>
        ))}
      </ul>
      <p className="muted dist-note">Bar = distance; faded rows have no box of their own near this object.</p>
    </>
  );
}

export function Inspector({
  pick,
  frame,
  summary,
  token,
  cache,
  evalPick = null,
  gtDetail = null,
  onClear,
}: {
  pick: PickRecord | null;
  frame: FrameDocument | null;
  summary: SceneSummary | null;
  token: string;
  cache: FrameCache;
  /** A clicked prediction wire; shown instead of a ground-truth box. */
  evalPick?: EvalPick | null;
  /** The clicked ground-truth box's status per output and who else sees it. */
  gtDetail?: GtEvalDetail | null;
  /** Clear the selection. */
  onClear?: () => void;
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
      <h3>
        Selected
        {(current || evalPick) && onClear ? (
          <button type="button" className="link side-card-action" onClick={onClear}>
            Clear
          </button>
        ) : null}
      </h3>
      {evalPick ? <PredictionInspector pick={evalPick} frame={frame} /> : null}
      {!evalPick && (!current || !frame) ? (
        <PanelState
          compact
          kind="empty"
          title={PANEL_DEFINITIONS.inspector.empty.title}
          description={PANEL_DEFINITIONS.inspector.empty.description}
        />
      ) : null}
      {!evalPick && current && frame ? (
        <>
          <p className="sel-title">
            <strong>{current.category.charAt(0).toUpperCase() + current.category.slice(1)}</strong>
            <span className="num">{current.trackId ?? ""}</span>
          </p>
          <dl className="stat-grid">
            <div>
              <dt>Speed</dt>
              <dd className="num">{formatKmh(objectSpeed)}</dd>
            </div>
            <div>
              <dt>Size l×w×h</dt>
              <dd className="num">
                {current.size.map((value) => value.toFixed(1)).join(" × ")} m
              </dd>
            </div>
            <div className="span-2">
              <dt>Annotated by</dt>
              <dd>
                {agentLabel(summary, current.agentId)}
                {agentLabel(summary, current.agentId) !== current.agentId ? (
                  <span className="muted num"> · {current.agentId}</span>
                ) : null}
              </dd>
            </div>
          </dl>
          <DistanceRows current={current} frame={frame} summary={summary} />
          {gtDetail ? <GtEvalRows detail={gtDetail} /> : null}
        </>
      ) : null}
    </section>
  );
}

const RADAR_INK: Record<string, string> = {
  vehicle: "#F58A1F",
  uav: "#3C8DFF",
  infrastructure: "#B7B1A6",
};

export function Radar({
  frame,
  pick,
  types,
  names,
  focus = null,
  open = true,
  onToggle,
}: {
  frame: FrameDocument | null;
  pick: PickRecord | null;
  types: Map<string, string>;
  /** `agent_id -> display name`. */
  names?: Map<string, string>;
  /** The agent to centre on (the camera strip's); else the first vehicle with a pose. */
  focus?: string | null;
  open?: boolean;
  onToggle?: () => void;
}) {
  const focused = focus ? frame?.agents.find((agent) => agent.agent_id === focus && isPose(agent.pose)) : undefined;
  const vehicle =
    focused ??
    frame?.agents.find((agent) => types.get(agent.agent_id) === "vehicle" && isPose(agent.pose)) ??
    frame?.agents.find((agent) => isPose(agent.pose));
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
  const centreName = vehicle ? (names?.get(vehicle.agent_id) ?? vehicle.agent_id) : null;
  return (
    <section className="side-card radar-card" aria-label="Bird's-eye radar, 80 metres">
      <h3>
        Radar · {range} m
        {onToggle ? (
          <button type="button" className="link side-card-action" aria-expanded={open} onClick={onToggle}>
            {open ? "Hide" : "Show"}
          </button>
        ) : null}
      </h3>
      {open ? (
        <div className="radar-body">
          <svg viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Agents and boxes within 80 metres, north up">
            <rect width={size} height={size} rx="8" className="radar-bg" />
            {[20, 40, 60, 80].map((ring) => (
              <circle key={ring} cx={half} cy={half} r={((ring / range) * (half - 10))} fill="none" className="radar-ring" />
            ))}
            <path d={`M${half} 12 V${size - 12} M12 ${half} H${size - 12}`} className="radar-ring" />
            <text x={half + 4} y="18" className="radar-text" fontSize="9">N</text>
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
              return (
                <circle
                  key={`${box.agent_id}-${index}`}
                  cx={point[0]}
                  cy={point[1]}
                  r={selected ? 3.5 : 1.6}
                  className={selected ? "radar-picked" : "radar-box"}
                />
              );
            })}
            {(frame?.agents ?? []).map((agent) => {
              if (!isPose(agent.pose)) {
                return null;
              }
              const point = place(agent.pose.tx, agent.pose.ty);
              if (!point) {
                return null;
              }
              return (
                <circle
                  key={agent.agent_id}
                  cx={point[0]}
                  cy={point[1]}
                  r="4"
                  fill={RADAR_INK[types.get(agent.agent_id) ?? ""] ?? "#B7B1A6"}
                />
              );
            })}
          </svg>
          <p className="muted">
            {centreName ? `Centred on ${centreName}, north up.` : "No agent has a pose at this sample."} Rings every 20 m.
          </p>
        </div>
      ) : null}
    </section>
  );
}
