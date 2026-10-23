import { BevCanvas } from "../backdrop/BevCanvas";
import { IllustrativeLabel } from "./IllustrativeLabel";
import { StoryShell } from "./StoryShell";

export function TwoAgents() {
  return (
    <StoryShell
      id="agents"
      kicker="One clock"
      title="Two agents."
      lede="In this illustrative scene the car’s ring draws out to 45 m. The drone is 24 m up, and its ring draws out to 55 m."
      keyProgress={1}
    >
      {(story) => (
        <div className="relative min-h-[22rem] flex-1">
          <BevCanvas
            bev="agents"
            label="Illustrative scene. The car ring draws in, then the drone and its ring."
            active={story.active}
            reduced={story.reduced}
            sample={() => {
              const p = story.progress.current;
              const vehicleRing = Math.min(1, p / 0.45);
              const droneMix = p < 0.4 ? 0 : Math.min(1, (p - 0.4) / 0.25);
              const droneRing = p < 0.62 ? 0 : Math.min(1, (p - 0.62) / 0.38);
              return {
                t: 8,
                options: {
                  vehicleRing,
                  droneRing,
                  showDrone: droneMix > 0.02,
                  alpha: 1,
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
