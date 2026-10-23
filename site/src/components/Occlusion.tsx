import { useRef } from "react";
import { BevCanvas } from "../backdrop/BevCanvas";
import { getBackdrop } from "../backdrop/sceneCache";
import { hiddenFromCar, occlusionTime, recoveredCount } from "../backdrop/story";
import { IllustrativeLabel } from "./IllustrativeLabel";
import { StoryShell } from "./StoryShell";

export function Occlusion() {
  const counterRef = useRef<HTMLParagraphElement>(null);
  const last = useRef("");
  const pulse = useRef({ p: -1, at: 0 });

  return (
    <StoryShell
      id="occlusion"
      kicker="Centrepiece"
      title="The occlusion moment."
      lede="A parked bus blocks the car’s view. Bring the drone in and each miss turns into a hit. The count belongs to this illustrative scene, not to a model."
      keyProgress={1}
    >
      {(story) => (
        <div className="relative min-h-[22rem] flex-1">
          <BevCanvas
            bev="occlusion"
            label="Illustrative scene. A bus blocks the car. The drone recovers the misses."
            active={story.active}
            reduced={story.reduced}
            sample={() => {
              const scene = getBackdrop();
              const t = occlusionTime(scene);
              const reveal = story.progress.current;
              const rows = hiddenFromCar(scene, t);
              const recovered = recoveredCount(rows, reveal);
              const now = performance.now();
              if (pulse.current.p !== reveal) {
                pulse.current.p = reveal;
                pulse.current.at = now;
              }
              const pulsing = !story.reduced && reveal > 0 && reveal < 1 && now - pulse.current.at < 1200;
              const missed = rows.length - recovered;
              const text =
                rows.length === 0
                  ? "No hidden objects in this frame"
                  : missed === 0
                    ? `${recovered} recovered`
                    : recovered === 0
                      ? `${missed} missed`
                      : `${missed} missed → ${recovered} recovered`;
              if (counterRef.current && last.current !== text) {
                counterRef.current.textContent = text;
                last.current = text;
              }
              return {
                t,
                options: {
                  droneReveal: reveal,
                  detection: "fused",
                  showSightLines: reveal < 0.98,
                  pulseClock: pulsing ? now / 1000 : 0.4,
                  showGhosts: false,
                  drift: false,
                },
              };
            }}
          />
          <IllustrativeLabel />
          <p
            ref={counterRef}
            className="num pointer-events-none absolute bottom-6 left-6 text-3xl font-medium text-primary sm:text-4xl"
          >
            Missed → recovered
          </p>
        </div>
      )}
    </StoryShell>
  );
}
