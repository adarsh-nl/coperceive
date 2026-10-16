import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

export function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduce(media.matches);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);
  return reduce;
}

export function Rise({ text }: { text: string }) {
  const reduce = useReducedMotion();
  const words = text.split(" ");
  return (
    <span className={reduce ? "rise rise-still" : "rise"}>
      {words.map((word, index) => (
        <span key={`${word}-${index}`} style={{ animationDelay: `${index * 70}ms` }}>
          {word}
        </span>
      ))}
    </span>
  );
}

export function Tilt({ children }: { children: ReactNode }) {
  const reduce = useReducedMotion();
  const [tilt, setTilt] = useState({ x: 0, y: 0 });
  function move(event: ReactPointerEvent<HTMLDivElement>) {
    if (reduce) {
      return;
    }
    const box = event.currentTarget.getBoundingClientRect();
    const px = (event.clientX - box.left) / box.width - 0.5;
    const py = (event.clientY - box.top) / box.height - 0.5;
    setTilt({ x: py * -7, y: px * 8 });
  }
  return (
    <div
      className="tilt"
      onPointerMove={move}
      onPointerLeave={() => setTilt({ x: 0, y: 0 })}
      style={{ transform: `rotateX(${tilt.x}deg) rotateY(${tilt.y}deg)` }}
    >
      {children}
    </div>
  );
}

export function Magnetic({ children }: { children: ReactNode }) {
  const reduce = useReducedMotion();
  const [shift, setShift] = useState({ x: 0, y: 0 });
  function move(event: ReactPointerEvent<HTMLSpanElement>) {
    if (reduce) {
      return;
    }
    const box = event.currentTarget.getBoundingClientRect();
    const dx = event.clientX - (box.left + box.width / 2);
    const dy = event.clientY - (box.top + box.height / 2);
    const reach = 90;
    if (Math.hypot(dx, dy) > reach) {
      setShift({ x: 0, y: 0 });
      return;
    }
    setShift({ x: dx * 0.18, y: dy * 0.18 });
  }
  return (
    <span className="magnetic" onPointerMove={move} onPointerLeave={() => setShift({ x: 0, y: 0 })}>
      <span style={{ transform: `translate(${shift.x}px, ${shift.y}px)` }}>{children}</span>
    </span>
  );
}

export function Scrubber({
  value,
  label,
  onChange,
}: {
  value: number;
  label: string;
  onChange: (ratio: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  function seek(clientX: number) {
    const box = ref.current?.getBoundingClientRect();
    if (!box || box.width <= 0) {
      return;
    }
    onChange(Math.min(1, Math.max(0, (clientX - box.left) / box.width)));
  }
  return (
    <div className="scrub">
      <div
        className="scrub-track"
        ref={ref}
        role="slider"
        tabIndex={0}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(value * 100)}
        aria-valuetext={label}
        aria-label="Sample"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          seek(event.clientX);
        }}
        onPointerMove={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            seek(event.clientX);
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") {
            onChange(Math.min(1, value + 0.02));
          } else if (event.key === "ArrowLeft") {
            onChange(Math.max(0, value - 0.02));
          }
        }}
      >
        <i style={{ width: `${value * 100}%` }} />
        <b style={{ left: `${value * 100}%` }} />
      </div>
      <span className="num">{label}</span>
    </div>
  );
}

export function ImageCompare({ before, after }: { before: string; after: string }) {
  const [at, setAt] = useState(56);
  const [broken, setBroken] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  if (broken) {
    return null;
  }
  function seek(clientX: number) {
    const box = ref.current?.getBoundingClientRect();
    if (!box || box.width <= 0) {
      return;
    }
    setAt(Math.min(96, Math.max(4, ((clientX - box.left) / box.width) * 100)));
  }
  return (
    <div
      className="compare"
      ref={ref}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        seek(event.clientX);
      }}
      onPointerMove={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) {
          seek(event.clientX);
        }
      }}
    >
      <img src={after} alt="Town03-000 in chase" onError={() => setBroken(true)} />
      <img src={before} alt="Town03-000 in orbit" style={{ clipPath: `inset(0 ${100 - at}% 0 0)` }} onError={() => setBroken(true)} />
      <span className="compare-handle" style={{ left: `${at}%` }} aria-hidden="true" />
      <span className="compare-tag left">Orbit</span>
      <span className="compare-tag right">Chase</span>
    </div>
  );
}

export function DotGlobe() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reduce = useReducedMotion();
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return;
    }
    const dots: { x: number; y: number; z: number }[] = [];
    const count = 220;
    for (let index = 0; index < count; index += 1) {
      const y = 1 - (index / (count - 1)) * 2;
      const radius = Math.sqrt(1 - y * y);
      const theta = index * 2.399963;
      dots.push({ x: Math.cos(theta) * radius, y, z: Math.sin(theta) * radius });
    }
    let frame = 0;
    let spin = 0;
    const draw = () => {
      const width = canvas.clientWidth || 1;
      const height = canvas.clientHeight || 1;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, width, height);
      const cx = width / 2;
      const cy = height / 2;
      const scale = Math.min(width, height) * 0.38;
      const cos = Math.cos(spin);
      const sin = Math.sin(spin);
      for (const dot of dots) {
        const x = dot.x * cos - dot.z * sin;
        const z = dot.x * sin + dot.z * cos;
        const depth = (z + 1.4) / 2.4;
        ctx.globalAlpha = 0.25 + depth * 0.75;
        ctx.fillStyle = "#1c1c1c";
        ctx.beginPath();
        ctx.arc(cx + x * scale, cy + dot.y * scale, 1.1 + depth, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    };
    const tick = () => {
      spin += 0.004;
      draw();
      frame = window.requestAnimationFrame(tick);
    };
    if (reduce) {
      draw();
      return;
    }
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [reduce]);
  return <canvas ref={canvasRef} className="globe" aria-hidden="true" />;
}

export function Particles() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reduce = useReducedMotion();
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return;
    }
    const specks = Array.from({ length: 36 }, () => ({
      x: Math.random(),
      y: Math.random(),
      r: 0.6 + Math.random() * 1.4,
      v: 0.00015 + Math.random() * 0.00035,
    }));
    let frame = 0;
    const draw = () => {
      const width = canvas.clientWidth || 1;
      const height = canvas.clientHeight || 1;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "#1c1c1c";
      for (const speck of specks) {
        ctx.globalAlpha = 0.25;
        ctx.beginPath();
        ctx.arc(speck.x * width, speck.y * height, speck.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    };
    const tick = () => {
      for (const speck of specks) {
        speck.y -= speck.v;
        if (speck.y < 0) {
          speck.y = 1;
        }
      }
      draw();
      frame = window.requestAnimationFrame(tick);
    };
    if (reduce) {
      draw();
      return;
    }
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [reduce]);
  return <canvas ref={canvasRef} className="particles" aria-hidden="true" />;
}
