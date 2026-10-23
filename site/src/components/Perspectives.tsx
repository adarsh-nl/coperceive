import { useEffect, useRef, type MutableRefObject, type RefObject } from "react";
import type { BevRenderOptions } from "../backdrop/drawBev";
import { BevCanvas } from "../backdrop/BevCanvas";
import { getBackdrop } from "../backdrop/sceneCache";
import { drawAddedBand, occlusionTime } from "../backdrop/story";
import { IllustrativeLabel } from "./IllustrativeLabel";
import { StoryShell } from "./StoryShell";

const views: { bev: string; title: string; options: BevRenderOptions }[] = [
  {
    bev: "vehicle",
    title: "Vehicle",
    options: {
      detection: "vehicle",
      lidar: "vehicle",
      showDrone: false,
      droneRing: 0,
      vehicleRing: 1,
      follow: "vehicle",
      showGhosts: false,
    },
  },
  {
    bev: "drone",
    title: "Drone",
    options: {
      detection: "drone",
      lidar: "drone",
      showVehicle: true,
      vehicleRing: 0,
      droneRing: 1,
      follow: "drone",
      showSightLines: false,
      showGhosts: false,
    },
  },
  {
    bev: "together",
    title: "Together",
    options: {
      detection: "fused",
      droneReveal: 1,
      follow: "both",
      showGhosts: false,
      showSightLines: false,
    },
  },
];

export function Perspectives() {
  const bandRef = useRef<HTMLCanvasElement>(null);

  return (
    <StoryShell
      id="perspectives"
      kicker="Same sample"
      title="Two perspectives."
      lede="Vehicle, drone, and both, side by side. Teal in the band is what only the drone sees in this illustrative scene. It is not a recall or mAP figure."
      keyProgress={1}
    >
      {(story) => (
        <PerspectiveBody storyActive={story.active} reduced={story.reduced} progress={story.progress} bandRef={bandRef} />
      )}
    </StoryShell>
  );
}

function PerspectiveBody({
  storyActive,
  reduced,
  progress,
  bandRef,
}: {
  storyActive: boolean;
  reduced: boolean;
  progress: MutableRefObject<number>;
  bandRef: RefObject<HTMLCanvasElement | null>;
}) {
  useEffect(() => {
    const canvas = bandRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return;
    let raf = 0;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      const w = Math.max(1, rect.width);
      const h = Math.max(1, rect.height);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#000000";
      ctx.fillRect(0, 0, w, h);
      drawAddedBand(ctx, w, h, getBackdrop(), reduced ? 1 : progress.current);
    };
    const loop = () => {
      draw();
      if (storyActive && !reduced) raf = requestAnimationFrame(loop);
    };
    loop();
    return () => cancelAnimationFrame(raf);
  }, [storyActive, reduced, progress, bandRef]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 px-6 pb-6 sm:px-10">
      <div className="grid min-h-[18rem] flex-1 grid-cols-1 gap-3 md:grid-cols-3">
        {views.map((view) => (
          <figure key={view.bev} className="relative min-h-52 overflow-hidden rounded-2xl border border-hairline">
            <BevCanvas
              bev={view.bev}
              label={`Illustrative ${view.title.toLowerCase()} view.`}
              active={storyActive}
              reduced={reduced}
              sample={() => ({
                t: occlusionTime(getBackdrop()) + (progress.current - 0.5) * 1.2,
                options: view.options,
              })}
            />
            <figcaption className="pointer-events-none absolute bottom-3 left-3 text-xs text-secondary">
              {view.title}
            </figcaption>
          </figure>
        ))}
      </div>
      <div className="relative h-20 overflow-hidden rounded-2xl border border-hairline">
        <canvas
          ref={bandRef}
          data-bev="band"
          role="img"
          aria-label="Illustrative band of moments only the drone sees. Not a measured recall."
          className="h-full w-full"
        />
        <IllustrativeLabel />
      </div>
    </div>
  );
}
