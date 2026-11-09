import { useEffect, useRef, useState, type ComponentType } from "react";
import type { ProductTourProps } from "./ProductTour.tsx";

/** Loads the player when the section nears the viewport, so the hero stays light. */
export function TourSlot() {
  const ref = useRef<HTMLDivElement>(null);
  const [Tour, setTour] = useState<ComponentType<ProductTourProps> | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      void import("./ProductTour.tsx").then((mod) => {
        if (!cancelled) setTour(() => mod.ProductTour);
      });
    };
    const el = ref.current;
    if (!el || !("IntersectionObserver" in window)) {
      load();
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        observer.disconnect();
        load();
      },
      { rootMargin: "400px" },
    );
    observer.observe(el);
    return () => {
      cancelled = true;
      observer.disconnect();
    };
  }, []);

  return (
    <div ref={ref}>
      {Tour ? (
        <Tour />
      ) : (
        <div aria-hidden="true" style={{ width: "100%", aspectRatio: "1440 / 900", borderRadius: 14, background: "#0b0b0c" }} />
      )}
    </div>
  );
}
