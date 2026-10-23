import { useEffect, useRef, useState, type ComponentType } from "react";
import { useMotionMode } from "../motion/useStory";

const STATS = [
  { value: "2", label: "agents" },
  { value: "9", label: "cameras" },
  { value: "149", label: "samples" },
  { value: "14.8 s", label: "first frame to last" },
  { value: "10 Hz", label: "clock" },
];

const poster = `${import.meta.env.BASE_URL}hero/poster.webp`;

export function RealData() {
  const ref = useRef<HTMLElement>(null);
  const { reduced } = useMotionMode();
  const [active, setActive] = useState(false);
  const [Clip, setClip] = useState<ComponentType<{ active: boolean }> | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || reduced) return;
    let cancelled = false;
    const io = new IntersectionObserver(
      ([entry]) => {
        setActive(entry.isIntersecting);
        if (!entry.isIntersecting) return;
        void import("./RealClip").then((mod) => {
          if (!cancelled) setClip(() => mod.RealClip);
        });
      },
      { rootMargin: "320px" },
    );
    io.observe(el);
    return () => {
      cancelled = true;
      io.disconnect();
    };
  }, [reduced]);

  return (
    <section id="data" ref={ref} data-pin="false" data-story="data" className="border-t border-hairline">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:px-10">
        <p className="text-xs font-medium uppercase tracking-[0.16em] text-tertiary">Town03-000</p>
        <h2 className="mt-3 max-w-3xl text-4xl font-semibold tracking-tight sm:text-5xl">
          Real data, not a mock-up.
        </h2>
        <p className="mt-4 max-w-2xl text-lg leading-relaxed text-secondary">
          Griffin 25 m scene <span translate="no">Town03-000</span>. The map name is{" "}
          <span translate="no">Carla/Maps/Town03</span>. Recordings are not stored in this
          repository. The player is a 100-frame excerpt exported with the page.
        </p>
        <dl className="mt-10 grid grid-cols-2 gap-3 sm:grid-cols-5">
          {STATS.map((stat) => (
            <div key={stat.label} className="rounded-2xl border border-hairline bg-glass px-4 py-4 backdrop-blur-22">
              <dt className="text-xs text-tertiary">{stat.label}</dt>
              <dd className="num mt-1 text-2xl text-primary">{stat.value}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-10">
          {reduced || !Clip ? (
            <img
              src={poster}
              alt="Still of Town03-000 in the coperceive viewer: lidar, boxes, and the town map."
              width={1920}
              height={1080}
              loading="lazy"
              className="aspect-video w-full rounded-3xl border border-hairline object-cover"
            />
          ) : (
            <Clip active={active} />
          )}
        </div>
        <p className="mt-4 text-sm text-tertiary">
          Map lines in the clip are from CARLA Town03. CARLA map assets, CC-BY. The OpenDRIVE file
          is not in the repository.
        </p>
      </div>
    </section>
  );
}
