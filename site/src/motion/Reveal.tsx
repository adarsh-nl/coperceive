import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Rises 32 px and fades in once, when the block enters the viewport
 * (landing-page-design B7). Reduced motion keeps the fade and drops the rise
 * (see `.reveal` in index.css).
 */
export function Reveal({ children, className = "" }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!("IntersectionObserver" in window)) {
      setShown(true);
      return;
    }
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true);
          io.disconnect();
        }
      },
      { threshold: 0.15 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <div ref={ref} className={`reveal ${shown ? "is-in" : ""} ${className}`}>
      {children}
    </div>
  );
}
