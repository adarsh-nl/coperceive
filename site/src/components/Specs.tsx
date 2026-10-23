import { useEffect } from "react";

const CARDS: { id: string; title: string; body: string; command?: string }[] = [
  {
    id: "spec-griffin",
    title: "Griffin download and convert",
    body: "The dev preset is drone metadata, vehicle metadata, and vehicle LiDAR. Camera archives are selected only with --components. The download writes outside the git checkout. --dry-run prints sizes and does not write the licence acknowledgement.",
    command: `coperceive download griffin --subset 25m --preset dev --dest /path/outside/the/repo --dry-run
coperceive convert griffin --subset 25m --raw-root /path/griffin-release --out /path/scenes`,
  },
  {
    id: "spec-validate",
    title: "Validate",
    body: "Writes a JSON report and a Markdown report: time sync, UAV height above the vehicle, pose speed and acceleration, shared-track and nearest-centre box agreement, coverage, and scene and frame counts. With a maps root it also measures distance from vehicle poses to the driving-lane centre. That centreline block does not fail the run.",
    command: "coperceive validate /path/scenes --json report.json --markdown report.md",
  },
  {
    id: "spec-viewer",
    title: "Viewer, five viewpoints",
    body: "The local page lists scene directories, plays recorded pose times, and can hide an agent. Viewpoints are orbit, bird’s-eye, chase, drone, and a camera frustum. A strip shows each camera image for the current sample.",
    command: "coperceive serve --root /path/to/a/workdir --maps /path/to/maps",
  },
  {
    id: "spec-eval",
    title: "Eval of predictions against ground truth",
    body: "Model output goes in a sidecar. eval run matches each output to that agent’s own ground truth in the world frame and writes matches and metrics. report and compare print one Markdown table. This page does not quote a model score.",
    command: `coperceive predictions import boxes.jsonl --run det-v1 --root /path/scenes --score-note "sigmoid"
coperceive eval run det-v1 --root /path/scenes
coperceive eval report det-v1 --root /path/scenes`,
  },
  {
    id: "spec-maps",
    title: "HD maps",
    body: "fetch extracts Town03, Town06, Town07, and Town10HD from the CARLA 0.9.15 package. The OpenDRIVE bytes are not committed. ingest writes a sidecar. align stores the transform from vehicle poses.",
    command: `coperceive maps fetch --dest /path/outside/the/repo
coperceive maps ingest --xodr /path/Town03.xodr --map-name Carla/Maps/Town03 --out /path/maps
coperceive maps align --maps /path/maps --scenes /path/scenes`,
  },
  {
    id: "spec-export",
    title: "Export MP4, GIF, PNG, and Rerun",
    body: "export sequence writes one camera to MP4, GIF, or a PNG sequence. MP4 needs ffmpeg. export rerun writes a .rrd and needs the viewer extra. The page can also save the live 3D view.",
    command: `coperceive export rerun /path/scene --out /path/scene.rrd
coperceive export sequence /path/scene --out /path/front.mp4 --agent vehicle --sensor front`,
  },
  {
    id: "spec-desktop",
    title: "macOS desktop app",
    body: "ADR 0014 specifies a Tauri v2 app for Apple Silicon: a local Python sidecar, and HPC over SSH. There is no GitHub Release. This page does not link to an Actions artifact.",
  },
  {
    id: "spec-slurm",
    title: "HPC Slurm mode",
    body: "coperceive model run --slurm emits an sbatch script for the same run and prints it. It does not submit unless you also pass --submit. Slurm status in the desktop design is specified separately and is not a download on this page.",
    command:
      "coperceive model run griffin --model instance-fusion --scenes /path/scenes --raw-root /path/raw --out /path/predictions --run-id det-coop --slurm",
  },
];

export function Specs() {
  useEffect(() => {
    const id = window.location.hash.replace("#", "");
    const el = id ? document.getElementById(id) : null;
    if (el instanceof HTMLDetailsElement) el.open = true;
  }, []);

  return (
    <section id="specs" data-pin="false" data-story="specs" className="border-t border-hairline">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:px-10">
        <p className="text-xs font-medium uppercase tracking-[0.16em] text-tertiary">In the repository</p>
        <h2 className="mt-3 text-4xl font-semibold tracking-tight sm:text-5xl">What’s inside.</h2>
        <p className="mt-4 max-w-2xl text-lg text-secondary">
          Open a card for the command that exists today. Nothing here is a roadmap item.
        </p>
        <div className="mt-10 border-t border-hairline">
          {CARDS.map((card) => (
            <details
              key={card.id}
              id={card.id}
              className="group border-b border-hairline"
              onToggle={(event) => {
                const el = event.currentTarget;
                if (el.open) history.replaceState(null, "", `#${el.id}`);
                else if (window.location.hash === `#${el.id}`) history.replaceState(null, "", "#specs");
              }}
            >
              <summary className="flex min-h-14 cursor-pointer list-none items-center justify-between gap-4 py-3 text-lg text-primary transition-colors duration-150 hover:text-white [&::-webkit-details-marker]:hidden">
                {card.title}
                <span aria-hidden="true" className="text-tertiary transition-transform duration-150 motion-reduce:transition-none group-open:rotate-45">
                  +
                </span>
              </summary>
              <div className="max-w-3xl pb-6 text-sm leading-relaxed text-secondary">
                <p>{card.body}</p>
                {card.command ? (
                  <pre
                    translate="no"
                    className="mt-4 overflow-x-auto rounded-2xl border border-hairline bg-glass p-4 font-mono text-[13px] leading-relaxed text-primary"
                  >
                    <code>{card.command}</code>
                  </pre>
                ) : null}
              </div>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}
