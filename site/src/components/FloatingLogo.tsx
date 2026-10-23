import { useEffect, useRef } from "react";
import { Wordmark } from "./Wordmark";

/** Sits over the hero and fades with scroll. Not sticky. */
export function FloatingLogo() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      const fade = Math.max(0, 1 - window.scrollY / window.innerHeight);
      el.style.opacity = String(fade);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  return (
    <div
      ref={ref}
      data-logo
      className="absolute left-[max(1.25rem,env(safe-area-inset-left))] top-[max(1.25rem,env(safe-area-inset-top))] z-20"
    >
      <Wordmark blink />
    </div>
  );
}
