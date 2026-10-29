import { useState } from "react";
import { loadAppearance, saveAppearance, setThemeOverride, type ThemeName } from "../viewer/appearance";
import Viewer from "../viewer/Viewer";
import { DotGlobe, ImageCompare, Magnetic, Particles, Rise, Scrubber, Tilt } from "./motion";
import "./landing.css";

const FEATURES = [
  {
    title: "Multi-agent",
    body: "The vehicle and the drone share one scrubber. Trails stay on the agents, and the drone keeps a leader down to the ground.",
  },
  {
    title: "Maps",
    body: "Lane centres and road edges come from the CARLA OpenDRIVE sidecar and sit under the lidar, thin and unbloomed.",
  },
  {
    title: "Cameras",
    body: "Each frustum’s far plane shows that camera’s frame at the playhead. The picture-in-picture panel stays beside it.",
  },
  {
    title: "Export",
    body: "One dialog writes a sequence, a still, a bird’s-eye plot, a camera overlay, or a Rerun file.",
  },
  {
    title: "Playback",
    body: "Play follows the recorded pose gap, from 0.25× to 4×, with step, loop, and a clock in seconds.",
  },
];

export default function Landing({
  token,
  theme,
  onTheme,
  onOpen,
}: {
  token: string;
  theme: ThemeName;
  onTheme: (theme: ThemeName) => void;
  onOpen: () => void;
}) {
  const [clock, setClock] = useState({ ratio: 0, label: "0.0 s" });
  const [scrub, setScrub] = useState<number | null>(null);
  const base = import.meta.env.BASE_URL;
  return (
    <div className="landing">
      <header className="topbar landing-bar">
        <a className="brand" href="#/">
          <span className="brand-mark" aria-hidden="true" />
          coperceive
        </a>
        <div className="top-cluster top-end">
          <button
            type="button"
            className="icon ghost"
            aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"}
            aria-pressed={theme === "dark"}
            onClick={() => onTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Moon /> : <Sun />}
          </button>
          <Magnetic>
            <button type="button" className="solid" onClick={onOpen}>
              Open the viewer
            </button>
          </Magnetic>
        </div>
      </header>
      <main>
        <section className="hero">
          <div className="hero-copy">
            <Particles />
            <p className="eyebrow">Cooperative perception</p>
            <h1>
              <Rise text="Inspect both agents on one clock." />
            </h1>
            <p className="lede shimmer">
              A Griffin scene, in the browser: lidar, boxes, the town map, and every camera, sample by sample.
            </p>
            <Magnetic>
              <button type="button" className="solid" onClick={onOpen}>
                Open the viewer
              </button>
            </Magnetic>
          </div>
          <div className="hero-stage">
            <Viewer token={token} slot={null} actions={null} poster scrubTo={scrub} onClock={setClock} />
            <div className="blur-stack" aria-hidden="true">
              <i /><i /><i /><i />
            </div>
            <Scrubber
              value={clock.ratio}
              label={clock.label}
              onChange={(ratio) => setScrub(ratio)}
            />
          </div>
        </section>
        <section className="feature-grid" aria-label="What the viewer does">
          {FEATURES.map((feature) => (
            <Tilt key={feature.title}>
              <article className="feature-card">
                <h2>{feature.title}</h2>
                <p>{feature.body}</p>
              </article>
            </Tilt>
          ))}
        </section>
        <section className="dataset">
          <div>
            <p className="eyebrow">Dataset</p>
            <h2>Griffin, vehicle and drone.</h2>
            <p>
              The 25 m dev tier is a vehicle and a UAV in CARLA, published as <span className="mono">wjh-svm/Griffin</span>.
              Coperceive converts a scene into schema 1: poses, lidar, cameras, and boxes on one clock. The map is a
              CARLA OpenDRIVE sidecar. Recordings stay outside this repository.
            </p>
            <ImageCompare before={`${base}landing/orbit.png`} after={`${base}landing/chase.png`} />
          </div>
          <DotGlobe />
        </section>
        <section className="cta">
          <h2>Town03 is one click in.</h2>
          <p>The viewer opens on the scenes under this server. Light is the default. The token, if you set one, stays in this tab.</p>
          <Magnetic>
            <button type="button" className="solid" onClick={onOpen}>
              Open the viewer
            </button>
          </Magnetic>
        </section>
      </main>
    </div>
  );
}

export function readTheme(): ThemeName {
  return loadAppearance().theme;
}

export function writeTheme(theme: ThemeName): void {
  setThemeOverride(theme);
  const current = loadAppearance();
  saveAppearance({ ...current, theme });
  document.documentElement.dataset.theme = theme;
}

function Sun() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <circle cx="8" cy="8" r="2.3" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path stroke="currentColor" strokeWidth="1.4" d="M8 1.5v1.7M8 12.8v1.7M1.5 8h1.7M12.8 8h1.7M3.3 3.3l1.2 1.2M11.5 11.5l1.2 1.2M12.7 3.3 11.5 4.5M4.5 11.5 3.3 12.7" />
    </svg>
  );
}

function Moon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
      <path fill="currentColor" d="M9.2 1.6a6.2 6.2 0 1 0 5.2 9.4 5.2 5.2 0 0 1-5.2-9.4z" />
    </svg>
  );
}
