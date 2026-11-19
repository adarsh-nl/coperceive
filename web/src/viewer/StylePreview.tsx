// SPDX-License-Identifier: Apache-2.0
import { useEffect, useRef } from "react";
import { renderStylePreview } from "./previewDraw.ts";
import type { ViewerStyle } from "./viewerStyle.ts";

/**
 * A live canvas of the shared preview scene in one style. Reduced motion
 * draws a single frame. Cinema drift turns the camera while it plays.
 */
export function StylePreview({
  style,
  drift,
  reducedMotion,
}: {
  style: ViewerStyle;
  drift: boolean;
  reducedMotion: boolean;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    let frame = 0;
    let raf = 0;
    const draw = () => {
      renderStylePreview(context, canvas.width, canvas.height, style, {
        frame,
        drift: style === "cinema" && drift && !reducedMotion,
      });
      frame += 1;
      if (!reducedMotion) raf = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [style, drift, reducedMotion]);
  return (
    <canvas
      ref={ref}
      className="viewer-style-preview"
      width={320}
      height={180}
      aria-hidden="true"
    />
  );
}
