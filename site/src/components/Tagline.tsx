import { useEffect, useRef } from "react";

const LINES = ["What one agent misses,", "another one sees."];

/**
 * Large type with the core benefit (landing-page-design B11). Each word goes
 * from muted to full colour, in reading order, as it crosses a line 60% down
 * the viewport. One scroll listener, throttled through requestAnimationFrame.
 * Reduced motion shows every word at full colour.
 */
export function Tagline() {
  const ref = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const words = Array.from(el.querySelectorAll<HTMLElement>("[data-word]"));
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      words.forEach((w) => w.classList.add("is-on"));
      return;
    }
    let frame = 0;
    const update = () => {
      frame = 0;
      const line = window.innerHeight * 0.6;
      const box = el.getBoundingClientRect();
      if (box.top > line) {
        words.forEach((w) => w.classList.remove("is-on"));
        return;
      }
      // Progress through the block maps to how many words are on.
      const progress = Math.min(1, (line - box.top) / Math.max(1, box.height * 0.9));
      const on = Math.ceil(progress * words.length);
      words.forEach((w, i) => w.classList.toggle("is-on", i < on));
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  return (
    <section id="tagline" data-pin="false" data-story="tagline" className="border-t border-hairline">
      <div className="mx-auto max-w-6xl px-6 py-24 sm:px-10 sm:py-24">
        <h2 ref={ref} className="max-w-[680px] text-4xl font-semibold tracking-tight sm:text-5xl lg:text-6xl">
          {LINES.map((line) => (
            <span key={line} className="block">
              {line.split(" ").map((word, i) => (
                <span key={`${word}-${i}`} data-word className="tagline-word">
                  {word}{" "}
                </span>
              ))}
            </span>
          ))}
        </h2>
      </div>
    </section>
  );
}
