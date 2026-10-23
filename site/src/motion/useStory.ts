import { useEffect, useRef, useState } from "react";

export interface MotionMode {
  reduced: boolean;
  mobile: boolean;
  pin: boolean;
}

function readMode(): MotionMode {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const mobile = window.matchMedia("(max-width: 767px)").matches;
  return { reduced, mobile, pin: !reduced && !mobile };
}

export function useMotionMode(): MotionMode {
  const [mode, setMode] = useState<MotionMode>(readMode);
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const mobile = window.matchMedia("(max-width: 767px)");
    const update = () => setMode(readMode());
    reduced.addEventListener("change", update);
    mobile.addEventListener("change", update);
    return () => {
      reduced.removeEventListener("change", update);
      mobile.removeEventListener("change", update);
    };
  }, []);
  return mode;
}

/**
 * Scroll progress for a pinned story section.
 * Desktop: progress follows scroll. Mobile: autoplay once. Reduced motion: hold keyProgress, no pin.
 */
export function useStory(keyProgress: number) {
  const sectionRef = useRef<HTMLElement>(null);
  const mode = useMotionMode();
  const progress = useRef(mode.reduced ? keyProgress : 0);
  const [active, setActive] = useState(false);

  useEffect(() => {
    const el = sectionRef.current;
    if (!el) return;
    if (mode.reduced) progress.current = keyProgress;

    let autoplay = 0;
    let played = false;
    const io = new IntersectionObserver(
      ([entry]) => {
        setActive(entry.isIntersecting);
        if (!entry.isIntersecting || !mode.mobile || mode.reduced || played) return;
        played = true;
        const t0 = performance.now();
        const dur = 4200;
        const step = (now: number) => {
          progress.current = Math.min(1, (now - t0) / dur);
          if (progress.current < 1) autoplay = requestAnimationFrame(step);
        };
        autoplay = requestAnimationFrame(step);
      },
      { threshold: 0.45 },
    );
    io.observe(el);

    const onScroll = () => {
      if (!mode.pin) return;
      const rect = el.getBoundingClientRect();
      const total = el.offsetHeight - window.innerHeight;
      progress.current = total <= 0 ? 1 : Math.min(1, Math.max(0, -rect.top / total));
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      io.disconnect();
      cancelAnimationFrame(autoplay);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [mode.pin, mode.mobile, mode.reduced, keyProgress]);

  return { sectionRef, progress, active, ...mode };
}
