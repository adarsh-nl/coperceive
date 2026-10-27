/** Small helpers shared by the landing canvases (from the "Landing Motion" design). */
export const MONO = '"Geist Mono Variable", ui-monospace, monospace';
export const SANS = '"Geist Variable", system-ui, sans-serif';

export const hex = (h: string) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
export const ss = (a: number, b: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
/** Eases through `arr` as `s` passes each integer step. */
export const seq = (s: number, arr: number[]) => {
  let r = arr[0];
  for (let i = 1; i < arr.length; i++) r = lerp(r, arr[i], ss(i - 0.2, i + 0.2, s));
  return r;
};
