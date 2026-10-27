import { Reveal } from "../motion/Reveal";

/** Tick positions (percent) for each row of the schematic. Not data: decoration only. */
const ROWS = [
  { label: "Vehicle", color: "bg-vehicle", ticks: [4, 14, 24, 34, 44, 54, 64, 74, 84, 94] },
  { label: "Drone", color: "bg-drone", ticks: [9, 19, 29, 39, 49, 59, 69, 79, 89] },
  { label: "Detections", color: "bg-lanes", ticks: [14, 34, 44, 64, 84] },
];

export function Timeline() {
  return (
    <section id="timeline" data-pin="false" data-story="timeline" className="border-t border-hairline">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:px-10">
        <Reveal>
          <h2 className="max-w-[680px] text-4xl font-semibold tracking-tight sm:text-5xl">
            Every sensor. One timeline.
          </h2>
          <p className="mt-4 max-w-[680px] text-lg text-secondary">
            Lidar, cameras and detections from each agent, played back together.
          </p>
        </Reveal>
        <Reveal className="mt-12">
          <div aria-hidden="true" className="rounded-2xl border border-hairline bg-surface p-6 sm:p-8">
            <div className="relative flex flex-col gap-6">
              {ROWS.map((row) => (
                <div key={row.label} className="flex items-center gap-4">
                  <span className="w-24 shrink-0 text-sm text-secondary">{row.label}</span>
                  <span className="relative h-2 flex-1 rounded-full bg-white/5">
                    {row.ticks.map((t) => (
                      <span
                        key={t}
                        className={`absolute top-0 h-2 w-1 rounded-full ${row.color}`}
                        style={{ left: `${t}%` }}
                      />
                    ))}
                  </span>
                </div>
              ))}
              <span className="timeline-track pointer-events-none absolute inset-y-0 left-28 right-0">
                <span className="timeline-playhead absolute inset-y-[-8px] left-0 w-0.5 rounded-full bg-primary" />
              </span>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
