import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { EvalChoice, EvalScene } from "./evalScene";
import type { StoryScene } from "./story";

/**
 * The landing page from the Claude Design file "Landing Motion": a scroll-pinned
 * story over a simulated city, the pipeline, install, an evaluate demo and a
 * footer. Both canvases are simulations; see site/FACTS.md.
 */

const REPO = "https://github.com/adarsh-nl/coperceive";
const DOCS = `${REPO}/tree/main/docs`;
const MONO_CSS = "var(--font-mono)";
const INSTALL = `$ pip install -e ".[app,griffin]"
$ coperceive convert griffin --subset 25m --raw-root ./griffin-release --out ./scenes
$ coperceive serve --root ./scenes
→ http://127.0.0.1:8731`;

function Wordmark({ size }: { size: number }) {
  return (
    <span translate="no" style={{ display: "inline-flex", alignItems: "baseline", fontFamily: MONO_CSS, fontWeight: 500, fontSize: size, letterSpacing: "-0.02em" }}>
      coperceive
      <span data-cursor="1" className="lm-cursor" style={{ display: "inline-block", width: "0.55em", height: "0.17em", marginLeft: "0.12em", borderRadius: 1, background: "#E03A22" }} />
    </span>
  );
}

const STEP_TAG: CSSProperties = { fontFamily: MONO_CSS, fontSize: 13, color: "#E03A22" };
const STEP_H2: CSSProperties = { margin: 0, fontSize: "clamp(36px,3.8vw,56px)", lineHeight: 1.02, letterSpacing: "-0.035em", fontWeight: 500 };
const STEP_P: CSSProperties = { margin: 0, fontSize: 18, lineHeight: 1.55, color: "#c7c7cc" };
const STEP_BOX: CSSProperties = { position: "absolute", left: "var(--gx)", top: "50%", maxWidth: 440, display: "flex", flexDirection: "column", gap: 18, opacity: 0 };

const STEPS = [
  { tag: "01 / 04 — one car", title: "One car sees what's in front of it.", body: "Its lidar stops at the first wall. Everything behind the buildings stays a grey outline — there, but unseen." },
  { tag: "02 / 04 — more cars", title: "Add cars. Fill the corners.", body: "Each vehicle keeps its own sensors, poses and boxes. coperceive keeps them in one scene instead of one log per car." },
  { tag: "03 / 04 — drones", title: "Drones see over the roofs.", body: "Objects seen by more than one agent turn white and link to every agent that saw them — a shared track, only when the id is actually shared." },
  { tag: "04 / 04 — one clock", title: "Every sensor, one timeline.", body: "Frames from every agent land on one integer-microsecond clock. Scrub it, hide an agent, export to Rerun or video." },
];

const CARDS = [
  { n: "01", verb: "ingest", col: "#F58A1F", hover: "lm-card-orange", title: "Any dataset, one manifest", body: "A YAML file with units, rotations and axes written out. Griffin, OPV2V and V2XSet are built in.", cmd: "adapters ingest my.yaml" },
  { n: "02", verb: "validate", col: "#4CC9F0", hover: "lm-card-cyan", title: "Check before you train", body: "Time sync, pose speed, cross-agent box agreement and coverage, as JSON and Markdown.", cmd: "validate ./scenes --strict" },
  { n: "03", verb: "view", col: "#3C8DFF", hover: "lm-card-blue", title: "Every agent in 3D", body: "Lidar, camera strips and HD-map lanes. Orbit, bird's-eye, chase, drone and camera views.", cmd: "serve --root ./scenes" },
  { n: "04", verb: "evaluate", col: "#8E6CEF", hover: "lm-card-violet", title: "Score in the world frame", body: "Match predictions per agent, put precision and recall on the timeline, overlay a second run.", cmd: "eval compare v1 v2" },
];

function Toggle({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      style={{ border: 0, cursor: "pointer", font: "inherit", padding: "9px 16px", borderRadius: 999, transition: "background-color 200ms, color 200ms", background: on ? "#f5f5f7" : "transparent", color: on ? "#000" : "#a1a1a6" }}
    >
      {children}
    </button>
  );
}

export function Landing() {
  const storyRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hudRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const capRefs = useRef<(HTMLDivElement | null)[]>([]);
  const restRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<HTMLPreElement>(null);
  const evalSecRef = useRef<HTMLElement>(null);
  const evalRef = useRef<HTMLCanvasElement>(null);
  const evalStatsRef = useRef<HTMLDivElement>(null);
  const [choice, setChoice] = useState<EvalChoice>({ iou: 0.5, run: "v1" });
  const choiceRef = useRef(choice);
  choiceRef.current = choice;

  // Both canvases on one animation loop.
  useEffect(() => {
    const canvas = canvasRef.current, story = storyRef.current, evalCanvas = evalRef.current, evalSec = evalSecRef.current;
    if (!canvas || !story || !evalCanvas || !evalSec) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Both scenes are their own chunks: the page's text paints before either canvas
    // starts, and the evaluate scene (below the fold) loads after the story.
    let scene: StoryScene | null = null;
    let evalScene: EvalScene | null = null;
    let disposed = false;
    void import("./story").then((mod) => {
      if (disposed) return;
      scene = new mod.StoryScene(
        { canvas, story, hud: hudRef.current, bar: barRef.current, captions: capRefs.current },
        { cars: 3, drones: 2, autoRotate: !reduced, reduced },
      );
      return import("./evalScene");
    }).then((mod) => {
      if (!mod || disposed) return;
      if (disposed) return;
      evalScene = new mod.EvalScene({ canvas: evalCanvas, section: evalSec, stats: evalStatsRef.current }, reduced);
    });
    let last = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      evalScene?.step(now, dt, choiceRef.current);
      scene?.tick(dt);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      scene?.dispose();
      evalScene?.dispose();
    };
  }, []);

  // Sections below the story rise in once; the install block types itself out.
  useEffect(() => {
    const root = restRef.current;
    if (!root) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced || !("IntersectionObserver" in window)) return;
    const els = [...root.querySelectorAll<HTMLElement>("[data-reveal]")];
    const vh = window.innerHeight;
    els.forEach((el) => {
      if (el.getBoundingClientRect().top > vh) {
        el.style.opacity = "0";
        el.style.transform = "translateY(32px)";
        el.dataset.hidden = "1";
      }
    });
    const term = termRef.current;
    let typer = 0;
    let typed = false;
    const timers: number[] = [];
    const io = new IntersectionObserver(
      (entries) =>
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          const el = e.target as HTMLElement;
          io.unobserve(el);
          if (el.dataset.hidden) {
            const d = (Number(el.dataset.reveal) || 0) * 90;
            el.style.transition = `opacity 800ms cubic-bezier(.2,.7,.2,1) ${d}ms, transform 800ms cubic-bezier(.2,.7,.2,1) ${d}ms, border-color 300ms, background-color 300ms`;
            requestAnimationFrame(() => {
              el.style.opacity = "1";
              el.style.transform = "";
            });
            timers.push(window.setTimeout(() => { el.style.transition = ""; }, 900 + d));
          }
          if (term && el.contains(term) && !typed) {
            typed = true;
            let n = 0;
            term.textContent = "";
            typer = window.setInterval(() => {
              n += INSTALL[n] === "\n" ? 1 : 2;
              term.textContent = INSTALL.slice(0, n) + (n < INSTALL.length ? "▍" : "");
              if (n >= INSTALL.length) window.clearInterval(typer);
            }, 28);
          }
        }),
      { threshold: 0.25 },
    );
    els.forEach((el) => io.observe(el));
    return () => {
      io.disconnect();
      window.clearInterval(typer);
      timers.forEach((t) => window.clearTimeout(t));
    };
  }, []);

  return (
    <div className="lm-root" style={{ background: "#000", color: "#f5f5f7" }}>
      <a href="#pipeline" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-full focus:bg-primary focus:px-4 focus:py-2 focus:text-black">
        Skip to content
      </a>
      <div ref={storyRef} data-screen-label="Story" style={{ position: "relative", height: "600vh" }}>
        <div style={{ position: "sticky", top: 0, height: "100vh", overflow: "hidden" }}>
          <canvas
            ref={canvasRef}
            data-bev="hero"
            role="img"
            aria-label="A simulated city block. Cars with lidar and drones with cameras; objects seen by more than one agent turn white."
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", display: "block", touchAction: "pan-y" }}
          />
          <div style={{ position: "absolute", inset: 0, pointerEvents: "none", background: "linear-gradient(90deg,rgba(0,0,0,0.82) 0%,rgba(0,0,0,0.4) 32%,rgba(0,0,0,0) 54%)" }} />

          <header style={{ position: "absolute", top: 0, left: 0, right: 0, display: "flex", justifyContent: "space-between", alignItems: "center", padding: "28px var(--gx)" }}>
            <Wordmark size={20} />
            <nav aria-label="Page" className="lm-nav" style={{ display: "flex", alignItems: "center", gap: 28, fontSize: 14, color: "#a1a1a6" }}>
              <a href="#pipeline" className="lm-link lm-anchor">Pipeline</a>
              <a href="#install" className="lm-link lm-anchor">Install</a>
              <a href={REPO} className="lm-link" style={{ padding: "9px 16px", borderRadius: 999, border: "1px solid rgba(255,255,255,0.18)", color: "#f5f5f7" }}>GitHub</a>
            </nav>
          </header>

          <div ref={hudRef} className="lm-hud" aria-hidden="true" style={{ position: "absolute", top: 92, right: "var(--gx)", fontFamily: MONO_CSS, fontSize: 12, color: "#a1a1a6", letterSpacing: "0.02em", textAlign: "right", lineHeight: 1.7 }} />

          <div ref={(el) => { capRefs.current[0] = el; }} style={{ position: "absolute", left: "var(--gx)", right: "var(--gx)", bottom: "14vh", maxWidth: 640, display: "flex", flexDirection: "column", gap: 24 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, alignSelf: "flex-start", padding: "8px 14px 8px 12px", borderRadius: 999, background: "rgba(28,28,30,0.78)", border: "1px solid rgba(255,255,255,0.14)", fontSize: 13, color: "#d2d2d7" }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#E03A22" }} />
              The Mac app is coming soon
            </div>
            <h1 style={{ margin: 0, fontSize: "clamp(56px,7.4vw,120px)", lineHeight: 0.94, letterSpacing: "-0.045em", fontWeight: 500 }}>
              Many agents.
              <br />
              One scene.
            </h1>
            <p style={{ margin: 0, fontSize: "clamp(17px,1.4vw,21px)", lineHeight: 1.5, color: "#c7c7cc", maxWidth: 500, textWrap: "pretty" }}>
              Every car, drone and roadside unit in one world frame, on one clock. See what they saw — together.
            </p>
            <div style={{ fontFamily: MONO_CSS, fontSize: 12, color: "#86868b", letterSpacing: "0.08em" }}>SCROLL ↓</div>
          </div>

          {STEPS.map((step, i) => (
            <div key={step.tag} ref={(el) => { capRefs.current[i + 1] = el; }} data-step={i + 1} aria-hidden="true" style={STEP_BOX}>
              <div style={STEP_TAG}>{step.tag}</div>
              <h2 style={STEP_H2}>{step.title}</h2>
              <p style={STEP_P}>{step.body}</p>
            </div>
          ))}

          <div aria-hidden="true" style={{ position: "absolute", right: 24, top: "50%", transform: "translateY(-50%)", width: 2, height: 160, background: "rgba(255,255,255,0.1)", borderRadius: 2, overflow: "hidden" }}>
            <div ref={barRef} data-progress style={{ width: "100%", height: "0%", background: "#E03A22" }} />
          </div>
        </div>
      </div>

      <main ref={restRef}>
        <section id="pipeline" data-screen-label="Pipeline" style={{ padding: "160px var(--gx) 120px", maxWidth: 1360, margin: "0 auto", display: "flex", flexDirection: "column", gap: 64 }}>
          <div data-reveal="0" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 48, flexWrap: "wrap" }}>
            <h2 style={{ margin: 0, fontSize: "clamp(40px,4.6vw,72px)", lineHeight: 1, letterSpacing: "-0.04em", fontWeight: 500, maxWidth: 760 }}>From raw logs to a shared view.</h2>
            <p style={{ margin: 0, fontSize: 18, lineHeight: 1.55, color: "#a1a1a6", maxWidth: 380 }}>One Python package. Runs on your workstation or behind a JupyterHub proxy.</p>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 16 }}>
            {CARDS.map((card, i) => (
              <div key={card.n} data-reveal={i + 1} className={`lm-card ${card.hover}`}>
                <div style={{ display: "flex", justifyContent: "space-between", fontFamily: MONO_CSS, fontSize: 13 }}>
                  <span style={{ color: card.col }}>{card.n}</span>
                  <span style={{ color: "#86868b" }}>{card.verb}</span>
                </div>
                <h3 style={{ margin: 0, fontSize: 24, fontWeight: 500, letterSpacing: "-0.02em" }}>{card.title}</h3>
                <p style={{ margin: 0, fontSize: 16, lineHeight: 1.55, color: "#a1a1a6", flex: 1 }}>{card.body}</p>
                <code style={{ fontFamily: MONO_CSS, fontSize: 12, color: "#86868b" }}>{card.cmd}</code>
              </div>
            ))}
          </div>
        </section>

        <section id="install" data-screen-label="Install" style={{ padding: "80px var(--gx) 160px", maxWidth: 1360, margin: "0 auto", display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(340px,100%),1fr))", gap: 64, alignItems: "center" }}>
          <div data-reveal="0" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
            <h2 style={{ margin: 0, fontSize: "clamp(40px,4.6vw,72px)", lineHeight: 1, letterSpacing: "-0.04em", fontWeight: 500 }}>Three commands to your first scene.</h2>
            <p style={{ margin: 0, fontSize: 18, lineHeight: 1.55, color: "#a1a1a6", maxWidth: 440 }}>Python 3.10–3.13. Apache-2.0. Local by default — nothing leaves your machine.</p>
            <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 18px", borderRadius: 12, border: "1px solid rgba(255,255,255,0.12)", background: "#0a0a0c", maxWidth: 440 }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#E03A22", flexShrink: 0 }} />
              <span style={{ fontSize: 15, lineHeight: 1.45, color: "#d2d2d7" }}>
                Prefer an app? <span style={{ color: "#f5f5f7" }}>coperceive for Mac</span> is coming soon — same scenes, no terminal.
              </span>
            </div>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <a href={DOCS} className="lm-btn-primary">Read the docs</a>
              <a href={REPO} className="lm-btn-ghost">View on GitHub</a>
            </div>
          </div>
          <div data-reveal="1" style={{ borderRadius: 14, border: "1px solid rgba(255,255,255,0.14)", background: "#0a0a0c", overflow: "hidden" }}>
            <div aria-hidden="true" style={{ display: "flex", gap: 8, padding: "14px 18px", borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
              {[0, 1, 2].map((k) => <span key={k} style={{ width: 10, height: 10, borderRadius: "50%", background: "#2a2a2e" }} />)}
            </div>
            <pre ref={termRef} data-term aria-label="Install commands" style={{ margin: 0, padding: "24px 24px 28px", fontFamily: MONO_CSS, fontSize: 14, lineHeight: 1.95, color: "#f5f5f7", minHeight: 150, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {INSTALL}
            </pre>
          </div>
        </section>

        <section id="evaluate" ref={evalSecRef} data-screen-label="Evaluate" style={{ position: "relative", height: "260vh" }}>
          <div style={{ position: "sticky", top: 0, height: "100vh", overflow: "hidden" }}>
            <canvas
              ref={evalRef}
              data-eval
              role="img"
              aria-label="A simulated drive. Ground-truth boxes, predicted boxes, and hits, misses and false positives inside the evaluation range."
              style={{ position: "absolute", inset: 0, width: "100%", height: "100%", display: "block", touchAction: "pan-y", cursor: "grab" }}
            />
            <div style={{ position: "absolute", inset: 0, pointerEvents: "none", background: "linear-gradient(90deg,rgba(0,0,0,0.8) 0%,rgba(0,0,0,0.35) 30%,rgba(0,0,0,0) 52%),linear-gradient(180deg,#000 0%,rgba(0,0,0,0) 14%)" }} />
            <div style={{ position: "absolute", left: "var(--gx)", right: "var(--gx)", top: "15vh", maxWidth: 500, display: "flex", flexDirection: "column", gap: 22 }}>
              <div style={{ fontFamily: MONO_CSS, fontSize: 13, color: "#E03A22" }}>eval run · eval compare</div>
              <h2 style={{ margin: 0, fontSize: "clamp(40px,4.4vw,68px)", lineHeight: 1, letterSpacing: "-0.04em", fontWeight: 500 }}>Score every detection where it happened.</h2>
              <p style={{ margin: 0, fontSize: 18, lineHeight: 1.55, color: "#c7c7cc", maxWidth: 440 }}>
                Predictions match the agent's own ground truth in the world frame by BEV IoU. Precision and recall trail along the road as the drive goes on.
              </p>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", fontFamily: MONO_CSS, fontSize: 13 }}>
                <div role="group" aria-label="IoU threshold" style={{ display: "flex", gap: 4, padding: 4, borderRadius: 999, border: "1px solid rgba(255,255,255,0.16)", background: "rgba(28,28,30,0.7)" }}>
                  <Toggle on={choice.iou === 0.5} onClick={() => setChoice((c) => ({ ...c, iou: 0.5 }))}>IoU ≥ 0.5</Toggle>
                  <Toggle on={choice.iou === 0.7} onClick={() => setChoice((c) => ({ ...c, iou: 0.7 }))}>IoU ≥ 0.7</Toggle>
                </div>
                <div role="group" aria-label="Run" style={{ display: "flex", gap: 4, padding: 4, borderRadius: 999, border: "1px solid rgba(255,255,255,0.16)", background: "rgba(28,28,30,0.7)" }}>
                  <Toggle on={choice.run === "v1"} onClick={() => setChoice((c) => ({ ...c, run: "v1" }))}>det-v1</Toggle>
                  <Toggle on={choice.run === "v2"} onClick={() => setChoice((c) => ({ ...c, run: "v2" }))}>det-v2</Toggle>
                </div>
              </div>
            </div>
            <div ref={evalStatsRef} data-eval-stats className="lm-eval-stats" aria-live="off" style={{ position: "absolute", right: "var(--gx)", top: "15vh", display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 18, fontFamily: MONO_CSS, fontSize: 12, color: "#a1a1a6", textAlign: "right", pointerEvents: "none" }} />
            <div style={{ position: "absolute", left: "var(--gx)", right: "var(--gx)", bottom: 40, display: "flex", gap: 22, flexWrap: "wrap", fontFamily: MONO_CSS, fontSize: 12, color: "#a1a1a6", pointerEvents: "none" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}><span style={{ width: 12, height: 8, border: "1.5px solid #f5f5f7", borderRadius: 1 }} />ground truth</span>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}><span style={{ width: 12, height: 8, background: "rgba(245,245,247,0.4)", borderRadius: 1 }} />prediction</span>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}><span style={{ width: 8, height: 8, background: "#1FA3A3", borderRadius: 1 }} />hit</span>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}><span style={{ width: 8, height: 8, background: "#8E6CEF", borderRadius: 1 }} />miss</span>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}><span style={{ width: 8, height: 8, background: "#E0A431", borderRadius: 1 }} />false positive</span>
              <span style={{ color: "#86868b" }}>· drag to orbit · hover a box</span>
            </div>
          </div>
        </section>
      </main>

      <footer style={{ borderTop: "1px solid rgba(255,255,255,0.08)", padding: "32px var(--gx)", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
        <Wordmark size={18} />
        <span style={{ fontFamily: MONO_CSS, fontSize: 12, color: "#86868b" }}>multi-agent perception data</span>
      </footer>
    </div>
  );
}
