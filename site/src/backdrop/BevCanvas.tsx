import { useEffect, useRef } from "react";
import { drawBevScene, type BevRenderOptions } from "./drawBev";
import { getBackdrop } from "./sceneCache";

export interface BevFrame {
  t: number;
  options?: BevRenderOptions;
}

export function fitScale(w: number, h: number): number {
  const limit = Math.min(w, h);
  return Math.min(6.2, Math.max(2.4, (limit * 0.36) / 55));
}

export function BevCanvas({
  label,
  bev,
  active,
  reduced,
  sample,
  className = "",
}: {
  label: string;
  bev: string;
  active: boolean;
  reduced: boolean;
  sample: () => BevFrame;
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const sampleRef = useRef(sample);
  sampleRef.current = sample;
  const sizeRef = useRef({ w: 1, h: 1 });

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return;

    const draw = () => {
      const { w, h } = sizeRef.current;
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      const bw = Math.max(1, Math.round(w * dpr));
      const bh = Math.max(1, Math.round(h * dpr));
      if (canvas.width !== bw || canvas.height !== bh) {
        canvas.width = bw;
        canvas.height = bh;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#000000";
      ctx.fillRect(0, 0, w, h);
      const frame = sampleRef.current();
      drawBevScene(ctx, w, h, getBackdrop(), frame.t, {
        drift: false,
        ...frame.options,
        scale: frame.options?.scale ?? fitScale(w, h),
      });
    };

    const measure = () => {
      const rect = canvas.getBoundingClientRect();
      sizeRef.current = { w: Math.max(1, rect.width), h: Math.max(1, rect.height) };
      draw();
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);

    let raf = 0;
    if (active && !reduced) {
      const loop = () => {
        draw();
        raf = requestAnimationFrame(loop);
      };
      raf = requestAnimationFrame(loop);
    }

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [active, reduced]);

  return (
    <canvas
      ref={ref}
      data-bev={bev}
      role="img"
      aria-label={label}
      className={`block h-full w-full ${className}`}
    />
  );
}
