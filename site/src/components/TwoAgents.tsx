import { BevCanvas } from "../backdrop/BevCanvas";
import { IllustrativeLabel } from "./IllustrativeLabel";
import { StoryShell } from "./StoryShell";

export function TwoAgents() {
  return (
    <StoryShell id="agents" title="One clock." lede="Platforms share this scene." keyProgress={1}>
      {(story) => (
        <div className="relative min-h-[22rem] flex-1">
          <BevCanvas
            bev="agents"
            label="Illustrative scene. Platforms appear on one clock."
            active={story.active}
            reduced={story.reduced}
            sample={() => {
              const p = story.progress.current;
              const vehicleRing = Math.min(1, p / 0.34);
              const partnerMix = p < 0.28 ? 0 : Math.min(1, (p - 0.28) / 0.2);
              const partnerRing = p < 0.42 ? 0 : Math.min(1, (p - 0.42) / 0.22);
              const droneMix = p < 0.62 ? 0 : Math.min(1, (p - 0.62) / 0.16);
              const droneRing = p < 0.74 ? 0 : Math.min(1, (p - 0.74) / 0.26);
              return {
                t: 10,
                options: {
                  vehicleRing,
                  partnerRing,
                  droneRing,
                  showPartner: partnerMix > 0.02,
                  showDrone: droneMix > 0.02,
                  droneReveal: 0,
                  detection: "vehicle",
                  showGhosts: false,
                  showSightLines: false,
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
