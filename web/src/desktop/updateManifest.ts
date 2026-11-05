// SPDX-License-Identifier: Apache-2.0
/** Compare dotted numeric versions. Positive when `remote` is newer than `current`. */

export function compareVersions(current: string, remote: string): number {
  const parse = (value: string): number[] =>
    value
      .trim()
      .replace(/^v/i, "")
      .split(".")
      .map((part) => {
        const match = /^(\d+)/.exec(part);
        return match ? Number(match[1]) : 0;
      });
  const left = parse(current);
  const right = parse(remote);
  const width = Math.max(left.length, right.length);
  for (let index = 0; index < width; index += 1) {
    const delta = (right[index] ?? 0) - (left[index] ?? 0);
    if (delta !== 0) return delta;
  }
  return 0;
}

export interface UpdateOffer {
  readonly version: string;
  readonly notes: string;
  readonly url: string;
  readonly signature?: string;
}

export function parseUpdateManifest(data: unknown, currentVersion: string): UpdateOffer | null {
  if (!data || typeof data !== "object") return null;
  const record = data as Record<string, unknown>;
  const version = record.version;
  if (typeof version !== "string" || compareVersions(currentVersion, version) <= 0) {
    return null;
  }
  const notes = typeof record.notes === "string" ? record.notes : "";
  const platforms = record.platforms;
  if (!platforms || typeof platforms !== "object") return null;
  const darwin = (platforms as Record<string, unknown>)["darwin-aarch64"];
  if (!darwin || typeof darwin !== "object") return null;
  const url = (darwin as Record<string, unknown>).url;
  if (typeof url !== "string" || url.length === 0) return null;
  const signature =
    typeof (darwin as Record<string, unknown>).signature === "string"
      ? ((darwin as Record<string, unknown>).signature as string)
      : undefined;
  return { version, notes, url, signature };
}
