import { useEffect, useRef, type MutableRefObject, type RefObject } from "react";
import { getBackdrop } from "../backdrop/sceneCache";
import { drawLedger } from "../backdrop/story";
import { IllustrativeLabel } from "./IllustrativeLabel";
import { StoryShell } from "./StoryShell";

export function Ledger() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  return (
    <StoryShell
      id="ledger"
      kicker="Sample by sample"
      title="The ledger."
      lede="Each row is an object in the illustrative scene. Colour marks who saw it as the samples draw from left to right."
      keyProgress={1}
    >
      {(story) => (
        <LedgerCanvas
          canvasRef={canvasRef}
          active={story.active}
          reduced={story.reduced}
          progress={story.progress}
        />
      )}
    </StoryShell>
  );
}

function LedgerCanvas({
  canvasRef,
  active,
  reduced,
  progress,
}: {
  canvasRef: RefObject<HTMLCanvasElement | null>;
  active: boolean;
  reduced: boolean;
  progress: MutableRefObject<number>;
}) {
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return;
    let raf = 0;
    const draw = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      const w = Math.max(1, rect.width);
      const h = Math.max(1, rect.height);
      const bw = Math.round(w * dpr);
      const bh = Math.round(h * dpr);
      if (canvas.width !== bw || canvas.height !== bh) {
        canvas.width = bw;
        canvas.height = bh;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#000000";
      ctx.fillRect(0, 0, w, h);
      drawLedger(ctx, w, h, getBackdrop(), reduced ? 1 : progress.current);
    };
    const loop = () => {
      draw();
      if (active && !reduced) raf = requestAnimationFrame(loop);
    };
    loop();
    return () => cancelAnimationFrame(raf);
  }, [active, reduced, progress, canvasRef]);

  return (
    <div className="relative mx-6 mb-6 min-h-[22rem] flex-1 overflow-hidden rounded-2xl border border-hairline sm:mx-10">
      <canvas
        ref={canvasRef}
        data-bev="ledger"
        role="img"
        aria-label="Illustrative ledger of which agent saw each object."
        className="h-full w-full"
      />
      <IllustrativeLabel />
      <ul className="pointer-events-none absolute bottom-3 right-3 flex flex-wrap justify-end gap-x-3 gap-y-1 text-[11px] text-secondary">
        <li><span aria-hidden="true" className="mr-1 inline-block h-2 w-2 rounded-sm bg-hit" /> Both</li>
        <li><span aria-hidden="true" className="mr-1 inline-block h-2 w-2 rounded-sm bg-vehicle" /> Vehicle</li>
        <li><span aria-hidden="true" className="mr-1 inline-block h-2 w-2 rounded-sm bg-drone" /> Drone</li>
        <li><span aria-hidden="true" className="mr-1 inline-block h-2 w-2 rounded-sm bg-miss" /> Neither</li>
      </ul>
    </div>
  );
}
