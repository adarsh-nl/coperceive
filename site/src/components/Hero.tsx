import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useMotionMode } from "../motion/useStory";
import { FloatingLogo } from "./FloatingLogo";

const RealClip = lazy(() => import("./RealClip").then((mod) => ({ default: mod.RealClip })));
const poster = `${import.meta.env.BASE_URL}hero/poster.webp`;

export function Hero() {
  const { reduced } = useMotionMode();
  const [active, setActive] = useState(true);
  const [play, setPlay] = useState(false);
  const [ready, setReady] = useState(false);
  const rootRef = useRef<HTMLElement>(null);
  const onReady = useRef(() => setReady(true));

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const io = new IntersectionObserver(([entry]) => setActive(entry.isIntersecting), {
      threshold: 0.15,
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (reduced) return;
    const start = () => setPlay(true);
    // The poster is the first paint. The player starts once the browser is idle,
    // and not before the poster has had time to settle.
    let idleId = 0;
    const timer = globalThis.setTimeout(() => {
      const idle = window.requestIdleCallback?.bind(window);
      if (idle) idleId = idle(start, { timeout: 1500 });
      else start();
    }, 3500);
    return () => {
      globalThis.clearTimeout(timer);
      if (idleId) window.cancelIdleCallback(idleId);
    };
  }, [reduced]);

  return (
    <section id="hero" ref={rootRef} data-pin="false" data-story="hero" className="relative h-[100svh] min-h-[36rem] overflow-hidden">
      <img
        src={poster}
        alt="Recorded scene, illustrative second vehicle."
        width={1920}
        height={1080}
        fetchPriority="high"
        decoding="async"
        aria-hidden={ready}
        className="absolute inset-0 h-full w-full object-cover"
      />
      {play ? (
        <Suspense fallback={null}>
          <RealClip active={active} onReady={onReady.current} />
        </Suspense>
      ) : null}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-2/5 bg-gradient-to-t from-black to-transparent" />
      <FloatingLogo />
      <div className="relative z-10 flex h-full flex-col justify-end px-6 pb-16 sm:px-12 lg:px-16">
        <h1 className="max-w-4xl text-5xl font-semibold tracking-tight text-primary sm:text-6xl lg:text-7xl">
          Every agent. One view.
        </h1>
        <p className="mt-4 max-w-xl text-lg text-secondary sm:text-xl">
          Sensor data and perception, on one timeline.
        </p>
        <p className="mt-5 max-w-sm text-xs text-tertiary">Recorded scene, illustrative second vehicle</p>
      </div>
    </section>
  );
}
