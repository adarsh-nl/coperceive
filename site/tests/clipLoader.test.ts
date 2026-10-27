import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parseClip } from "../src/utils/clipLoader";
import { V1_AGENTS, legendEntries, manifestAgents, sceneDescription } from "../src/utils/agents";

const hero = new URL("../public/hero/", import.meta.url);

function committed(name: string): ArrayBuffer {
  const bytes = readFileSync(new URL(name, hero));
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

/** Minimal CPCVCLP2 writer following the layout in scripts/site/export_hero_clip.py. */
class Writer {
  private bytes: number[] = [];
  private view = new DataView(new ArrayBuffer(8));
  private put(n: number) {
    for (let i = 0; i < n; i++) this.bytes.push(this.view.getUint8(i));
  }
  u8(v: number) { this.view.setUint8(0, v); this.put(1); return this; }
  u16(v: number) { this.view.setUint16(0, v, true); this.put(2); return this; }
  i16(v: number) { this.view.setInt16(0, v, true); this.put(2); return this; }
  u32(v: number) { this.view.setUint32(0, v, true); this.put(4); return this; }
  u64(v: number) { this.view.setBigUint64(0, BigInt(v), true); this.put(8); return this; }
  f32(v: number) { this.view.setFloat32(0, v, true); this.put(4); return this; }
  ascii(s: string) { for (const c of s) this.bytes.push(c.charCodeAt(0)); return this; }
  buffer(): ArrayBuffer { return new Uint8Array(this.bytes).buffer; }
}

const FOUR = [
  { id: "car_a", kind: 0, rgb: [0xf5, 0x8a, 0x1f] },
  { id: "car_b", kind: 0, rgb: [0x4c, 0xc9, 0xf0] },
  { id: "car_c", kind: 0, rgb: [0x8e, 0x6c, 0xef] },
  { id: "uav", kind: 1, rgb: [0x3c, 0x8d, 0xff] },
];

function fourAgentClip(agents = FOUR, frames = 3): ArrayBuffer {
  const w = new Writer().ascii("CPCVCLP2").u16(2).f32(100).f32(50).f32(0);
  w.u8(agents.length);
  for (const a of agents) {
    w.u8(a.id.length).ascii(a.id).u8(a.kind).u8(a.rgb[0]).u8(a.rgb[1]).u8(a.rgb[2]);
  }
  w.u32(1).u32(frames);
  // one lane centre, two vertices: (100, 50, 0) and (110.5, 49, 0.25)
  w.u8(1).u16(2).i16(0).i16(0).i16(0).i16(1050).i16(-100).i16(25);
  for (let f = 0; f < frames; f++) {
    w.u64(1_000_000 + f * 100_000);
    agents.forEach((_, i) => {
      const missing = i === 2 && f === 1; // car_c has no pose in frame 1
      w.u8(missing ? 0 : 1);
      if (missing) [0, 0, 0, 0, 0, 0, 1].forEach((v) => w.f32(v));
      else [100 + i * 10 + f, 50, i === 3 ? 30 : 0, 0, 0, 0, 1].forEach((v) => w.f32(v));
      const n = missing ? 0 : i + 1;
      w.u32(n);
      for (let k = 0; k < n; k++) w.i16(k * 100).i16(-k * 100).i16(150);
    });
  }
  return w.buffer();
}

describe("parseClip, version 1 (the committed clip)", () => {
  const clip = parseClip(committed("clip.bin"));
  const manifest = JSON.parse(readFileSync(new URL("manifest.json", hero), "utf8"));

  it("keeps the fixed vehicle and drone table", () => {
    expect(clip.version).toBe(1);
    expect(clip.agents).toEqual(V1_AGENTS);
    expect(clip.origin).toEqual([-105, -25, 0]);
  });

  it("matches the manifest's counts", () => {
    expect(clip.frames).toHaveLength(manifest.num_frames);
    const total = clip.frames.reduce((n, f) => n + f.agents[0].points.length / 3, 0);
    expect(total).toBe(manifest.stats.total_lidar_points);
    expect(clip.frames[0].timeUs).toBe(manifest.time_start_us);
  });

  it("puts the sweep on the vehicle and none on the drone", () => {
    for (const frame of clip.frames) {
      expect(frame.agents).toHaveLength(2);
      expect(frame.agents[1].points).toHaveLength(0);
    }
    expect(clip.frames[0].agents[0].points.length).toBeGreaterThan(0);
    expect(clip.mapPositions.length).toBeGreaterThan(0);
  });
});

describe("parseClip, version 2", () => {
  const clip = parseClip(fourAgentClip());

  it("reads the agent table", () => {
    expect(clip.version).toBe(2);
    expect(clip.agents).toEqual([
      { id: "car_a", kind: "vehicle", color: "#F58A1F" },
      { id: "car_b", kind: "vehicle", color: "#4CC9F0" },
      { id: "car_c", kind: "vehicle", color: "#8E6CEF" },
      { id: "uav", kind: "drone", color: "#3C8DFF" },
    ]);
  });

  it("reads per-agent poses and points", () => {
    expect(clip.frames.map((f) => f.timeUs)).toEqual([1_000_000, 1_100_000, 1_200_000]);
    const f2 = clip.frames[2];
    expect(f2.agents[3].position).toEqual([132, 50, 30]);
    expect(f2.agents[3].quaternion).toEqual([0, 0, 0, 1]);
    // car_b, two points: origin + (0, 0, 1.5) and origin + (1, -1, 1.5)
    const pts = Array.from(f2.agents[1].points);
    [100, 50, 1.5, 101, 49, 1.5].forEach((v, i) => expect(pts[i]).toBeCloseTo(v, 4));
    expect(clip.frames[0].agents.map((a) => a.points.length / 3)).toEqual([1, 2, 3, 4]);
  });

  it("marks an agent without a pose", () => {
    expect(clip.frames[1].agents[2].present).toBe(false);
    expect(clip.frames[1].agents[2].points).toHaveLength(0);
    expect(clip.frames[1].agents[1].present).toBe(true);
  });

  it("reads the map in metres from the origin", () => {
    expect(clip.mapPositions).toHaveLength(6);
    expect(clip.mapPositions[3]).toBeCloseTo(110.5, 4);
    expect(clip.mapPositions[4]).toBeCloseTo(49, 4);
    expect(clip.mapPositions[5]).toBeCloseTo(0.25, 4);
  });

  it("refuses one agent, trailing bytes and an unknown magic", () => {
    expect(() => parseClip(fourAgentClip(FOUR.slice(0, 1), 1))).toThrow(/two or more/);
    const padded = new Uint8Array(fourAgentClip().byteLength + 1);
    padded.set(new Uint8Array(fourAgentClip()));
    expect(() => parseClip(padded.buffer)).toThrow(/trailing/);
    expect(() => parseClip(new Writer().ascii("NOTACLIP").u16(1).buffer())).toThrow(/magic/);
  });
});

describe("legend and label", () => {
  it("is Vehicle, Drone, Lane for the committed manifest", () => {
    const manifest = JSON.parse(readFileSync(new URL("manifest.json", hero), "utf8"));
    const entries = legendEntries(manifestAgents(manifest.agents));
    expect(entries.map((e) => e.label)).toEqual(["Vehicle", "Drone", "Lane"]);
    expect(entries.map((e) => e.color)).toEqual(["#F58A1F", "#3C8DFF", "#7dcea0"]);
    expect(sceneDescription(V1_AGENTS)).toBe("A vehicle and a drone.");
  });

  it("numbers repeated kinds for four agents", () => {
    const clip = parseClip(fourAgentClip());
    expect(legendEntries(clip.agents).map((e) => e.label)).toEqual([
      "Vehicle 1",
      "Vehicle 2",
      "Vehicle 3",
      "Drone",
      "Lane",
    ]);
    expect(sceneDescription(clip.agents)).toBe("Three vehicles and a drone.");
  });

  it("reads a version 2 manifest list", () => {
    const agents = manifestAgents([
      { id: "a", kind: "vehicle", color: "#F58A1F" },
      { id: "b", kind: "infrastructure", color: "#1FA3A3" },
    ]);
    expect(legendEntries(agents).map((e) => e.label)).toEqual(["Vehicle", "RSU", "Lane"]);
  });
});
