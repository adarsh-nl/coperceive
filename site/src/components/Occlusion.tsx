import { BevCanvas } from "../backdrop/BevCanvas";
import { getBackdrop } from "../backdrop/sceneCache";
import { occlusionTime } from "../backdrop/story";
import { IllustrativeLabel } from "./IllustrativeLabel";
import { StoryShell } from "./StoryShell";

export function Occlusion() {
  return (
    <StoryShell id="occlusion" title="The blind spot." lede="One view is blocked. Another is not." keyProgress={1}>
      {(story) => (
        <div className="relative min-h-[22rem] flex-1">
          <BevCanvas
            bev="occlusion"
            label="Illustrative scene. A blocked view, then another platform’s view."
            active={story.active}
            reduced={story.reduced}
            sample={() => {
              const reveal = story.progress.current;
              return {
                t: occlusionTime(getBackdrop()),
                options: {
                  droneReveal: reveal,
                  detection: "fused",
                  showSightLines: reveal < 0.98,
                  showGhosts: false,
                  drift: false,
                  pulseClock: 0.4,
                },
              };
            }}
          />
          <IllustrativeLabel />
        </div>
      )}
    </StoryShell>
  );
}
