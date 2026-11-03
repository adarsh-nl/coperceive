// SPDX-License-Identifier: Apache-2.0

/** The nearest prepared scene before or after `current` in `order`, skipping the rest. */
export function neighbourScene(
  order: readonly string[],
  ready: ReadonlySet<string>,
  current: string,
  direction: 1 | -1,
): { scene: string | null; skipped: string[] } {
  const skipped: string[] = [];
  let index = order.indexOf(current);
  if (index < 0) {
    return { scene: null, skipped };
  }
  for (index += direction; index >= 0 && index < order.length; index += direction) {
    const candidate = order[index];
    if (candidate === undefined) break;
    if (ready.has(candidate)) {
      return { scene: candidate, skipped };
    }
    skipped.push(candidate);
  }
  return { scene: null, skipped };
}
