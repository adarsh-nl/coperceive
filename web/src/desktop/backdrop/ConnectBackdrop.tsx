// SPDX-License-Identifier: Apache-2.0
// Looping animated BEV backdrop for the Coperceive desktop Connect window.
// Ported and adapted from "Viewer Directions.dc.html" (1b "Ledger") and "viewer-engine.js".

import { useEffect, useMemo, useRef } from "react";
import { createBackdropScene, crossfadeWeights } from "./scene";
import { drawBevScene } from "./drawBev";

interface ConnectBackdropProps {
  hasRecent?: boolean;
}

export default function ConnectBackdrop({ hasRecent = false }: ConnectBackdropProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const scene = useMemo(() => createBackdropScene(42), []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const mql =
      typeof window !== "undefined" && typeof window.matchMedia === "function"
        ? window.matchMedia("(prefers-reduced-motion: reduce)")
        : null;

    let animId = 0;
    let startTime = performance.now();
    let lastFrameTime = startTime;
    let isPaused = false;
    let blurTimer: ReturnType<typeof setTimeout> | null = null;

    const renderStatic = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = window.innerWidth;
      const h = window.innerHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#000000";
      ctx.fillRect(0, 0, w, h);
      drawBevScene(ctx, w, h, scene, 9.5, { scale: 6.2, showRibbon: h >= 700, drift: false });
    };

    const renderFrame = (now: number) => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = window.innerWidth;
      const h = window.innerHeight;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
      }

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "#000000";
      ctx.fillRect(0, 0, w, h);

      const elapsedSec = Math.max(0, (now - startTime) / 1000);
      const cf = crossfadeWeights(elapsedSec, scene.T_END, scene.BLEND_WINDOW);

      if (cf.isBlending) {
        // Offscreen blending for seamless loop wrap
        drawBevScene(ctx, w, h, scene, cf.t1, {
          scale: 6.2,
          showRibbon: h >= 700,
          drift: true,
          alpha: cf.w1,
        });
        drawBevScene(ctx, w, h, scene, cf.t2, {
          scale: 6.2,
          showRibbon: h >= 700,
          drift: true,
          alpha: cf.w2,
        });
      } else {
        drawBevScene(ctx, w, h, scene, cf.t1, {
          scale: 6.2,
          showRibbon: h >= 700,
          drift: true,
          alpha: 1.0,
        });
      }
    };

    const FPS_CAP = 30;
    const FRAME_INTERVAL = 1000 / FPS_CAP;

    const loop = (now: number) => {
      if (mql?.matches) {
        renderStatic();
        return;
      }
      if (isPaused) return;

      const delta = now - lastFrameTime;
      if (delta >= FRAME_INTERVAL) {
        lastFrameTime = now - (delta % FRAME_INTERVAL);
        renderFrame(now);
      }

      animId = requestAnimationFrame(loop);
    };

    const onMotionChange = (e: MediaQueryListEvent | MediaQueryList) => {
      if (e.matches) {
        cancelAnimationFrame(animId);
        renderStatic();
      } else {
        cancelAnimationFrame(animId);
        lastFrameTime = performance.now();
        animId = requestAnimationFrame(loop);
      }
    };

    if (mql) {
      if (typeof mql.addEventListener === "function") {
        mql.addEventListener("change", onMotionChange);
      } else if (typeof (mql as any).addListener === "function") {
        (mql as any).addListener(onMotionChange);
      }
    }

    const onResize = () => {
      if (mql?.matches) {
        renderStatic();
      }
    };
    window.addEventListener("resize", onResize);

    const onVisibilityChange = () => {
      if (document.hidden) {
        if (!blurTimer) {
          blurTimer = setTimeout(() => {
            isPaused = true;
          }, 10000);
        }
      } else {
        if (blurTimer) {
          clearTimeout(blurTimer);
          blurTimer = null;
        }
        if (isPaused) {
          isPaused = false;
          lastFrameTime = performance.now();
          if (!mql?.matches) {
            loop(performance.now());
          }
        }
      }
    };

    const onWindowBlur = () => {
      if (!blurTimer) {
        blurTimer = setTimeout(() => {
          isPaused = true;
        }, 10000);
      }
    };

    const onWindowFocus = () => {
      if (blurTimer) {
        clearTimeout(blurTimer);
        blurTimer = null;
      }
      if (isPaused) {
        isPaused = false;
        lastFrameTime = performance.now();
        if (!mql?.matches) {
          loop(performance.now());
        }
      }
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("blur", onWindowBlur);
    window.addEventListener("focus", onWindowFocus);

    if (mql?.matches) {
      renderStatic();
    } else {
      renderFrame(startTime);
      animId = requestAnimationFrame(loop);
    }

    return () => {
      cancelAnimationFrame(animId);
      if (blurTimer) clearTimeout(blurTimer);
      if (mql) {
        if (typeof mql.removeEventListener === "function") {
          mql.removeEventListener("change", onMotionChange);
        } else if (typeof (mql as any).removeListener === "function") {
          (mql as any).removeListener(onMotionChange);
        }
      }
      window.removeEventListener("resize", onResize);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("blur", onWindowBlur);
      window.removeEventListener("focus", onWindowFocus);
    };
  }, [scene]);

  return (
    <div
      aria-hidden="true"
      style={{
        position: "fixed",
        inset: 0,
        pointerEvents: "none",
        zIndex: 0,
        overflow: "hidden",
        backgroundColor: "#000000",
      }}
    >
      <canvas
        ref={canvasRef}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          display: "block",
          opacity: 0.65,
        }}
      />
      {/* 55% Black Scrim + Radial Vignette */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundColor: "rgba(0, 0, 0, 0.55)",
          backgroundImage: "radial-gradient(circle at center, transparent 30%, rgba(0, 0, 0, 0.85) 100%)",
        }}
      />
      {hasRecent && (
        <div
          style={{
            position: "absolute",
            bottom: "16px",
            left: "20px",
            fontSize: "11px",
            letterSpacing: "0.02em",
            color: "rgba(255, 255, 255, 0.45)",
            fontFamily: "Geist, system-ui, sans-serif",
            userSelect: "none",
          }}
        >
          Synthetic scene · illustrative
        </div>
      )}
    </div>
  );
}
