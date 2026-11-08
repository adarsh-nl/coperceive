// SPDX-License-Identifier: Apache-2.0
import { useEffect, useMemo, useRef, useState } from "react";
import specJson from "./tour_spec.json";
import {
  advance,
  chapterProgress,
  clock,
  eagerScreenKeys,
  frameAt,
  range,
  seekChapter,
  timeline,
  type Step,
} from "./engine.ts";
import "./tour.css";

type Screen = { key: string; alt: string; capture_output: string };

const spec = specJson as {
  chapters: string[];
  screens: Screen[];
  steps: Step[];
};

const LOOP_KEYS = new Set(
  spec.screens.filter((screen) => screen.capture_output.includes("f*.png")).map((screen) => screen.key),
);

const ALT = new Map(spec.screens.map((screen) => [screen.key, screen.alt]));
const EAGER = eagerScreenKeys(spec.steps);

export type ProductTourProps = {
  /** 1-based chapter. When set, the tour loops that chapter. */
  chapter?: number;
  controls?: boolean;
  autoplay?: boolean;
  /** Recording page: the stage fills 1920×1200 and the control row stays off. */
  record?: boolean;
};

function mediaBase(key: string): string {
  return `${import.meta.env.BASE_URL}tour/${key}`;
}

export function ProductTour({ chapter, controls = true, autoplay = true, record = false }: ProductTourProps) {
  const steps = spec.steps;
  const tl = useMemo(() => timeline(steps), [steps]);
  const span = range(tl, chapter);
  const spanRef = useRef(span);
  spanRef.current = span;

  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [playing, setPlaying] = useState(false);
  const [visible, setVisible] = useState(false);
  const [t, setT] = useState(span[0]);
  const tRef = useRef(t);
  const playingRef = useRef(playing);
  const visibleRef = useRef(visible);
  const reducedRef = useRef(reduced);
  tRef.current = t;
  playingRef.current = playing;
  visibleRef.current = visible;
  reducedRef.current = reduced;

  const rootRef = useRef<HTMLDivElement>(null);
  const videos = useRef<Record<string, HTMLVideoElement | null>>({});
  const shown = useRef<Set<string>>(new Set(EAGER));

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduced(media.matches);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    setPlaying(!reduced && autoplay);
  }, [reduced, autoplay]);

  useEffect(() => {
    const next = span[0];
    tRef.current = next;
    setT(next);
  }, [span[0]]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || !("IntersectionObserver" in window)) {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { threshold: 0.35 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      if (playingRef.current && visibleRef.current && !reducedRef.current && dt > 0 && dt < 0.5) {
        const next = advance(tRef.current, dt, spanRef.current);
        tRef.current = next;
        setT(next);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const frame = frameAt(steps, t);
  if (frame.screen) shown.current.add(frame.screen);
  const upcoming = tl[frame.idx + 1]?.screen;
  if (upcoming) shown.current.add(upcoming);
  const captionStep = tl[frame.capIdx];
  const caption = captionStep?.cap;

  useEffect(() => {
    for (const [key, node] of Object.entries(videos.current)) {
      if (!node) continue;
      if (key === frame.screen && playing && visible && !reduced) void node.play().catch(() => undefined);
      else node.pause();
    }
  }, [frame.screen, playing, visible, reduced]);

  const seek = (chapterIndex: number) => {
    const next = seekChapter(tl, chapterIndex);
    tRef.current = next;
    setT(next);
  };

  const total = tl[tl.length - 1]?.t1 ?? 0;
  const showPointer = frame.ptrOn && !frame.end;
  const capClass = [
    "tour-cap",
    captionStep?.capSide === "right" ? "is-right" : "is-left",
    captionStep?.capTop ? "is-top" : "is-bottom",
  ].join(" ");

  return (
    <div ref={rootRef} className={record ? "tour-root is-record" : "tour-root"}>
      <div className="tour-stage">
        <div className="tour-frame">
          <div
            className="tour-cam"
            style={{ transform: `translate(${frame.tx}%, ${frame.ty}%) scale(${frame.s})` }}
          >
            {spec.screens.map((screen) => {
              if (!shown.current.has(screen.key)) return null;
              const on = screen.key === frame.screen;
              const alt = ALT.get(screen.key) ?? "";
              const eager = EAGER.has(screen.key);
              if (LOOP_KEYS.has(screen.key)) {
                if (reduced) {
                  return (
                    <img
                      key={screen.key}
                      className="tour-shot"
                      src={`${mediaBase(screen.key)}.webp`}
                      alt={alt}
                      width={1800}
                      height={1124}
                      style={{ opacity: on ? 1 : 0 }}
                      aria-hidden={on ? undefined : true}
                    />
                  );
                }
                return (
                  <video
                    key={screen.key}
                    ref={(node) => {
                      videos.current[screen.key] = node;
                    }}
                    className="tour-shot"
                    poster={`${mediaBase(screen.key)}.webp`}
                    width={1800}
                    height={1124}
                    muted
                    playsInline
                    loop
                    preload={eager ? "auto" : "none"}
                    aria-label={alt}
                    style={{ opacity: on ? 1 : 0 }}
                    aria-hidden={on ? undefined : true}
                  >
                    <source src={`${mediaBase(screen.key)}.mp4`} type="video/mp4" />
                  </video>
                );
              }
              return (
                <img
                  key={screen.key}
                  className="tour-shot"
                  src={`${mediaBase(screen.key)}.webp`}
                  alt={alt}
                  width={2880}
                  height={1800}
                  loading={eager ? "eager" : "lazy"}
                  fetchPriority={screen.key === "library" ? "high" : "auto"}
                  decoding="async"
                  style={{ opacity: on ? 1 : 0 }}
                  aria-hidden={on ? undefined : true}
                />
              );
            })}
          </div>

          {frame.step.click && showPointer ? (
            <span key={`rip-${frame.idx}`} aria-hidden="true">
              <span
                className="tour-dot"
                style={{ left: `${frame.px}%`, top: `${frame.py}%`, animationDelay: `${frame.clickDelay}s` }}
              />
              <span
                className="tour-ripple"
                style={{ left: `${frame.px}%`, top: `${frame.py}%`, animationDelay: `${frame.clickDelay}s` }}
              />
              <span
                className="tour-ripple2"
                style={{
                  left: `${frame.px}%`,
                  top: `${frame.py}%`,
                  animationDelay: `${frame.clickDelay + 0.16}s`,
                }}
              />
            </span>
          ) : null}

          <span
            aria-hidden="true"
            className="tour-ptr"
            style={{ left: `${frame.px}%`, top: `${frame.py}%`, opacity: showPointer ? 1 : 0 }}
          >
            <span
              key={frame.idx}
              className={frame.step.click ? "tour-press" : undefined}
              style={frame.step.click ? { animationDelay: `${frame.clickDelay}s` } : undefined}
            >
              <svg width="30" height="30" viewBox="0 0 30 30" fill="none">
                <path
                  d="M4 4 L26 12.2 L15.6 15.6 L12.2 26 Z"
                  fill="#FF8A3D"
                  stroke="#FFFFFF"
                  strokeWidth="2"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
          </span>

          {frame.end ? (
            <div className="tour-end">
              <span
                style={{
                  fontFamily: "Geist Mono Variable, ui-monospace, monospace",
                  fontSize: "clamp(26px, 4.4vw, 56px)",
                  fontWeight: 500,
                  letterSpacing: "-0.02em",
                  color: "#FFFFFF",
                }}
              >
                coperceive
                <span style={{ color: "#FF8A3D" }}>_</span>
              </span>
              <span style={{ fontSize: "clamp(14px, 1.7vw, 20px)", color: "#C9C9CF", maxWidth: 640 }}>
                View and evaluate cooperative-perception datasets, on your laptop or your cluster.
              </span>
              <span
                style={{
                  fontFamily: "Geist Mono Variable, ui-monospace, monospace",
                  fontSize: "clamp(11px, 1.1vw, 14px)",
                  color: "#8B8B94",
                }}
              >
                Free and open source · Apache-2.0 · macOS, Windows, Linux
              </span>
            </div>
          ) : null}

          <span className="tour-chip">Synthetic demo data · simulated predictions</span>
        </div>

        {caption ? (
          <div key={caption[0]} className={capClass}>
            <span className="tour-kicker">{spec.chapters[captionStep.ch]}</span>
            <span className="tour-title">{caption[0]}</span>
            <span className="tour-body">{caption[1]}</span>
          </div>
        ) : null}
      </div>

      {controls ? (
        <div className="tour-controls">
          <button
            type="button"
            className="tour-btn tour-play"
            aria-label={playing ? "Pause tour" : "Play tour"}
            onClick={() => setPlaying((on) => !on)}
          >
            {playing ? (
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <rect x="3" y="2" width="3.5" height="12" rx="1" fill="currentColor" />
                <rect x="9.5" y="2" width="3.5" height="12" rx="1" fill="currentColor" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path d="M4 2.5 L13.5 8 L4 13.5 Z" fill="currentColor" />
              </svg>
            )}
          </button>
          <div className="tour-chapters" role="group" aria-label="Chapters">
            {spec.chapters.map((label, index) => {
              const current = frame.step.ch === index;
              const fill = `${Math.round(chapterProgress(tl, index, t) * 1000) / 10}%`;
              return (
                <button
                  key={label}
                  type="button"
                  className="tour-btn tour-chap"
                  aria-current={current ? "step" : undefined}
                  onClick={() => seek(index)}
                >
                  <span className="tour-chap-label" style={{ color: current ? "#F4F4F5" : "#A1A1AA" }}>
                    {index + 1}
                    {"  "}
                    {label}
                  </span>
                  <span aria-hidden="true" className="tour-track">
                    <span className="tour-fill" style={{ width: fill }} />
                  </span>
                </button>
              );
            })}
          </div>
          <span className="tour-clock">
            {clock(t)} / {clock(total)}
          </span>
        </div>
      ) : null}

      {record ? null : (
        <p className="tour-foot">
          Every screen is a capture of the real Coperceive app. The scene, cluster profile and predictions are synthetic examples, and the scores are simulated, not model results.
        </p>
      )}
    </div>
  );
}
