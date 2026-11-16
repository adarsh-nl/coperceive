// SPDX-License-Identifier: Apache-2.0
/** Non-personal WebGL failure diagnostics helper for the viewer fallback notice. */

export interface WebglDiagnostics {
  error: string;
  webgl: boolean;
  webgl2: boolean;
  vendor: string | null;
  renderer: string | null;
  unmaskedVendor: string | null;
  unmaskedRenderer: string | null;
  vertexHighFloatPrecision: number | null;
  devicePixelRatio: number;
}

interface DebugRendererInfo {
  UNMASKED_VENDOR_WEBGL: number;
  UNMASKED_RENDERER_WEBGL: number;
}

interface WebglContextLike {
  VENDOR: number;
  RENDERER: number;
  VERTEX_SHADER?: number;
  HIGH_FLOAT?: number;
  getParameter?: (pname: number) => unknown;
  getExtension?: (name: string) => unknown;
  getShaderPrecisionFormat?: (shaderType: number, precisionType: number) => { precision?: number } | null;
}

function asNullableText(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/**
 * Normalizes a thrown WebGL setup error and strips file paths, home
 * directories, or loopback/origin URLs so no personal details are kept.
 */
export function sanitizeWebglError(error: unknown): string {
  const raw = error instanceof Error ? error.message || error.name : String(error ?? "");
  const collapsed = raw.replace(/\s+/g, " ").trim();
  const scrubbed = collapsed
    .replace(/https?:\/\/\S+/gi, "[origin]")
    .replace(/file:\/\/\S+/gi, "[file]")
    .replace(/(?:\/Users|\/home)\/[^\s):'"]+/g, "[home]")
    .replace(/[A-Za-z]:\\Users\\[^\s):'"]+/g, "[home]");
  return scrubbed || "WebGL initialization failed";
}

function probeContext(canvas: HTMLCanvasElement, kind: "webgl2" | "webgl"): WebglContextLike | null {
  try {
    const ctx = canvas.getContext(kind);
    return ctx && typeof ctx === "object" ? (ctx as unknown as WebglContextLike) : null;
  } catch {
    return null;
  }
}

/**
 * Probes WebGL / WebGL2 capabilities and renderer/vendor metadata from the
 * canvas (or an offscreen probe canvas) without throwing on broken drivers.
 */
export function collectWebglDiagnostics(
  canvas: HTMLCanvasElement | null,
  error: unknown,
): WebglDiagnostics {
  let target: HTMLCanvasElement | null = canvas;
  if (!target && typeof document !== "undefined") {
    try {
      target = document.createElement("canvas");
    } catch {
      target = null;
    }
  }
  const offscreen =
    typeof document !== "undefined"
      ? (() => {
          try {
            return document.createElement("canvas");
          } catch {
            return null;
          }
        })()
      : null;

  const gl2 = (target ? probeContext(target, "webgl2") : null) ?? (offscreen ? probeContext(offscreen, "webgl2") : null);
  const gl1 = (target ? probeContext(target, "webgl") : null) ?? (offscreen ? probeContext(offscreen, "webgl") : null);
  const ctx = gl2 ?? gl1;

  let vendor: string | null = null;
  let renderer: string | null = null;
  let unmaskedVendor: string | null = null;
  let unmaskedRenderer: string | null = null;
  let vertexHighFloatPrecision: number | null = null;

  if (ctx) {
    try {
      vendor = asNullableText(ctx.getParameter?.(ctx.VENDOR));
    } catch {
      vendor = null;
    }
    try {
      renderer = asNullableText(ctx.getParameter?.(ctx.RENDERER));
    } catch {
      renderer = null;
    }
    try {
      const ext = ctx.getExtension?.("WEBGL_debug_renderer_info") as DebugRendererInfo | null | undefined;
      if (ext && typeof ext === "object") {
        unmaskedVendor = asNullableText(ctx.getParameter?.(ext.UNMASKED_VENDOR_WEBGL));
        unmaskedRenderer = asNullableText(ctx.getParameter?.(ext.UNMASKED_RENDERER_WEBGL));
      }
    } catch {
      unmaskedVendor = null;
      unmaskedRenderer = null;
    }
    try {
      if (typeof ctx.VERTEX_SHADER === "number" && typeof ctx.HIGH_FLOAT === "number") {
        const fmt = ctx.getShaderPrecisionFormat?.(ctx.VERTEX_SHADER, ctx.HIGH_FLOAT);
        if (fmt && typeof fmt.precision === "number") {
          vertexHighFloatPrecision = fmt.precision;
        }
      }
    } catch {
      vertexHighFloatPrecision = null;
    }
  }

  const dpr =
    typeof window !== "undefined" && typeof window.devicePixelRatio === "number" && Number.isFinite(window.devicePixelRatio)
      ? window.devicePixelRatio
      : 1;

  return {
    error: sanitizeWebglError(error),
    webgl: Boolean(gl1 || gl2),
    webgl2: Boolean(gl2),
    vendor,
    renderer,
    unmaskedVendor,
    unmaskedRenderer,
    vertexHighFloatPrecision,
    devicePixelRatio: dpr,
  };
}

/** One-line graphics vendor/renderer readout for the viewer fallback notice. */
export function formatWebglGraphicsReadout(diag: WebglDiagnostics): string {
  const vendor = diag.unmaskedVendor ?? diag.vendor ?? "null";
  const renderer = diag.unmaskedRenderer ?? diag.renderer ?? "null";
  return `Graphics readout: vendor ${vendor} · renderer ${renderer}`;
}

/** Formats PII-free WebGL diagnostics suitable for clipboard copy. */
export function formatWebglDiagnosticsText(diag: WebglDiagnostics): string {
  return [
    "Coperceive 3D view (WebGL) diagnostics",
    `error: ${diag.error}`,
    `webgl: ${String(diag.webgl)}`,
    `webgl2: ${String(diag.webgl2)}`,
    `vendor: ${diag.vendor ?? "null"}`,
    `renderer: ${diag.renderer ?? "null"}`,
    `unmaskedVendor: ${diag.unmaskedVendor ?? "unavailable"}`,
    `unmaskedRenderer: ${diag.unmaskedRenderer ?? "unavailable"}`,
    `vertexHighFloatPrecision: ${diag.vertexHighFloatPrecision === null ? "null" : String(diag.vertexHighFloatPrecision)}`,
    `devicePixelRatio: ${String(diag.devicePixelRatio)}`,
  ].join("\n");
}

/** Copies diagnostics text to the clipboard using Clipboard API with execCommand fallback. */
export async function copyWebglDiagnostics(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to execCommand copy in restricted contexts.
    }
  }
  if (typeof document === "undefined" || !document.body) {
    return false;
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  area.style.pointerEvents = "none";
  document.body.appendChild(area);
  try {
    area.select();
    return Boolean(document.execCommand?.("copy"));
  } catch {
    return false;
  } finally {
    area.remove();
  }
}
