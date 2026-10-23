import { useEffect, useRef, useState } from "react";
import { BevCanvas } from "../backdrop/BevCanvas";
import { useMotionMode } from "../motion/useStory";
import { CtaLink } from "./CtaLink";
import { IllustrativeLabel } from "./IllustrativeLabel";

export function Hero() {
  const { reduced } = useMotionMode();
  const [paused, setPaused] = useState(false);
  const [active, setActive] = useState(true);
  const rootRef = useRef<HTMLElement>(null);
  const clock = useRef(8);

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => setActive(entry.isIntersecting), {
      threshold: 0.15,
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const playing = active && !paused && !reduced;

  return (
    <section
      id="hero"
      ref={rootRef}
      data-pin="false"
      data-story="hero"
      className="relative h-[100svh] min-h-[36rem]"
    >
      <div className="absolute inset-0">
        <BevCanvas
          bev="hero"
          label="Illustrative bird’s-eye scene with a car, a drone, range rings, lidar, and boxes."
          active={playing}
          reduced={reduced || paused}
          sample={() => {
            if (playing) clock.current = 6 + ((performance.now() / 1000) % 14);
            return {
              t: reduced ? 12 : clock.current,
              options: { showRibbon: false, drift: playing },
            };
          }}
        />
      </div>
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black via-black/55 to-black/10" />
      <IllustrativeLabel className="top-20" />
      <div className="relative z-10 flex h-full flex-col justify-end px-6 pb-16 pt-24 sm:px-12 lg:px-16">
        <h1 className="max-w-4xl text-5xl font-semibold tracking-tight text-primary sm:text-6xl lg:text-7xl">
          See what your car can’t.
        </h1>
        <p className="mt-6 max-w-xl text-lg leading-relaxed text-secondary sm:text-xl">
          <span translate="no">coperceive</span> plays cooperative perception data — car and drone,
          one clock — and shows exactly what each agent sees and misses.
        </p>
        <div className="pointer-events-auto mt-8">
          <CtaLink showNote />
        </div>
      </div>
      {reduced ? null : (
        <button
          type="button"
          className="absolute bottom-6 right-6 z-10 inline-flex min-h-11 min-w-11 items-center justify-center rounded-full border border-hairline bg-glass px-4 text-sm text-primary backdrop-blur-22 transition-colors duration-150 hover:border-hairline-bright"
          aria-pressed={paused}
          onClick={() => setPaused((value) => !value)}
        >
          {paused ? "Play" : "Pause"}
        </button>
      )}
    </section>
  );
}
