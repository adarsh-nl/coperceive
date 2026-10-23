import { useEffect, useRef, useState } from "react";
import { BevCanvas } from "../backdrop/BevCanvas";
import { useMotionMode } from "../motion/useStory";
import { FloatingLogo } from "./FloatingLogo";

export function Hero() {
  const { reduced } = useMotionMode();
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

  const playing = active && !reduced;

  return (
    <section id="hero" ref={rootRef} data-pin="false" data-story="hero" className="relative h-[100svh] min-h-[36rem]">
      <div className="absolute inset-0">
        <BevCanvas
          bev="hero"
          label="Illustrative scene. Vehicles and a drone move together."
          active={playing}
          reduced={reduced}
          sample={() => {
            if (playing) clock.current = 6 + ((performance.now() / 1000) % 14);
            return {
              t: reduced ? 12 : clock.current,
              options: { showRibbon: false, showGhosts: false, showSightLines: false, drift: playing },
            };
          }}
        />
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-2/5 bg-gradient-to-t from-black to-transparent" />
      <FloatingLogo />
      <div className="relative z-10 flex h-full flex-col justify-end px-6 pb-16 sm:px-12 lg:px-16">
        <h1 className="max-w-4xl text-5xl font-semibold tracking-tight text-primary sm:text-6xl lg:text-7xl">
          See what your car can’t.
        </h1>
        <p className="mt-5 text-xs text-tertiary">Illustrative</p>
      </div>
    </section>
  );
}
