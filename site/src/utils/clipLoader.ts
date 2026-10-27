import type { AgentFrame, AgentKind, BoxRecord, ClipAgent, FrameData, ParsedClip } from "../types/clip";
import { tokens } from "../tokens";
import { V1_AGENTS } from "./agents";

const KINDS: AgentKind[] = ["vehicle", "drone", "infrastructure", "other"];

export async function loadClip(
  binUrl: string = `${import.meta.env.BASE_URL}hero/clip.bin`,
  signal?: AbortSignal
): Promise<ParsedClip> {
  const binRes = await fetch(binUrl, { signal });
  if (!binRes.ok) {
    throw new Error(`Failed to load binary clip: ${binRes.status}`);
  }
  return parseClip(await binRes.arrayBuffer());
}

/** Parse `CPCVCLIP` (version 1) or `CPCVCLP2` (version 2) bytes. */
export function parseClip(buffer: ArrayBuffer): ParsedClip {
  const view = new DataView(buffer);
  if (buffer.byteLength < 8) throw new Error("Clip is too short");
  const magic = String.fromCharCode(...new Uint8Array(buffer, 0, 8));
  if (magic !== "CPCVCLIP" && magic !== "CPCVCLP2") {
    throw new Error(`Invalid clip magic header: ${magic}`);
  }
  const v2 = magic === "CPCVCLP2";

  let offset = 8;
  offset += 2; // version
  const ox = view.getFloat32(offset, true); offset += 4;
  const oy = view.getFloat32(offset, true); offset += 4;
  const oz = view.getFloat32(offset, true); offset += 4;

  let agents: ClipAgent[] = V1_AGENTS;
  if (v2) {
    const count = view.getUint8(offset); offset += 1;
    if (count < 2) throw new Error(`Clip has ${count} agents; two or more are required`);
    const decoder = new TextDecoder();
    agents = [];
    for (let i = 0; i < count; i++) {
      const n = view.getUint8(offset); offset += 1;
      const id = decoder.decode(new Uint8Array(buffer, offset, n)); offset += n;
      const kind = KINDS[view.getUint8(offset)] ?? "other"; offset += 1;
      const rgb = [view.getUint8(offset), view.getUint8(offset + 1), view.getUint8(offset + 2)];
      offset += 3;
      const color = `#${rgb.map((c) => c.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
      agents.push({ id, kind, color });
    }
  }

  const numPolylines = view.getUint32(offset, true); offset += 4;
  const numFrames = view.getUint32(offset, true); offset += 4;

  // Unpack map polylines
  const mapPosList: number[] = [];
  const mapColList: number[] = [];

  const hexToRgb = (hex: string) => {
    const num = parseInt(hex.replace("#", ""), 16);
    return [(num >> 16) / 255, ((num >> 8) & 0xff) / 255, (num & 0xff) / 255];
  };
  const [laneR, laneG, laneB] = hexToRgb(tokens.lanes);
  const [edgeR, edgeG, edgeB] = hexToRgb(tokens.roadEdge);

  for (let i = 0; i < numPolylines; i++) {
    const layerId = view.getUint8(offset); offset += 1;
    const nverts = view.getUint16(offset, true); offset += 2;

    const r = layerId === 1 ? laneR : edgeR;
    const g = layerId === 1 ? laneG : edgeG;
    const b = layerId === 1 ? laneB : edgeB;

    let prevX = 0, prevY = 0, prevZ = 0;
    for (let v = 0; v < nverts; v++) {
      const dx = view.getInt16(offset, true); offset += 2;
      const dy = view.getInt16(offset, true); offset += 2;
      const dz = view.getInt16(offset, true); offset += 2;

      const px = ox + dx * 0.01;
      const py = oy + dy * 0.01;
      const pz = oz + dz * 0.01;

      if (v > 0) {
        mapPosList.push(prevX, prevY, prevZ, px, py, pz);
        mapColList.push(r, g, b, r, g, b);
      }
      prevX = px;
      prevY = py;
      prevZ = pz;
    }
  }

  const readPose = (): Pick<AgentFrame, "position" | "quaternion"> => {
    const tx = view.getFloat32(offset, true); offset += 4;
    const ty = view.getFloat32(offset, true); offset += 4;
    const tz = view.getFloat32(offset, true); offset += 4;
    const qx = view.getFloat32(offset, true); offset += 4;
    const qy = view.getFloat32(offset, true); offset += 4;
    const qz = view.getFloat32(offset, true); offset += 4;
    const qw = view.getFloat32(offset, true); offset += 4;
    return { position: [tx, ty, tz], quaternion: [qx, qy, qz, qw] };
  };

  const readPoints = (nPts: number): Float32Array => {
    const pts = new Float32Array(nPts * 3);
    for (let p = 0; p < nPts; p++) {
      const dx = view.getInt16(offset, true); offset += 2;
      const dy = view.getInt16(offset, true); offset += 2;
      const dz = view.getInt16(offset, true); offset += 2;
      pts[p * 3] = ox + dx * 0.01;
      pts[p * 3 + 1] = oy + dy * 0.01;
      pts[p * 3 + 2] = oz + dz * 0.01;
    }
    return pts;
  };

  // Unpack frames
  const frames: FrameData[] = [];
  for (let f = 0; f < numFrames; f++) {
    // uint64 time_us
    const low = view.getUint32(offset, true);
    const high = view.getUint32(offset + 4, true);
    const timeUs = high * 4294967296 + low;
    offset += 8;

    if (v2) {
      const perAgent: AgentFrame[] = [];
      for (let a = 0; a < agents.length; a++) {
        const present = (view.getUint8(offset) & 1) === 1; offset += 1;
        const pose = readPose();
        const nPts = view.getUint32(offset, true); offset += 4;
        perAgent.push({ present, ...pose, points: readPoints(nPts) });
      }
      frames.push({ timeUs, agents: perAgent, boxes: [] });
      continue;
    }

    // Version 1: vehicle pose, drone pose, vehicle lidar, boxes.
    const vehiclePose = readPose();
    const dronePose = readPose();
    const nPts = view.getUint16(offset, true); offset += 2;
    const lidarPoints = readPoints(nPts);

    const nBoxes = view.getUint16(offset, true); offset += 2;
    const boxes: BoxRecord[] = [];
    for (let b = 0; b < nBoxes; b++) {
      const trackId = view.getInt32(offset, true); offset += 4;
      const agentFlag = view.getUint8(offset); offset += 1;
      const category = view.getUint8(offset); offset += 1;
      const cx = view.getFloat32(offset, true); offset += 4;
      const cy = view.getFloat32(offset, true); offset += 4;
      const cz = view.getFloat32(offset, true); offset += 4;
      const l = view.getFloat32(offset, true); offset += 4;
      const w = view.getFloat32(offset, true); offset += 4;
      const h = view.getFloat32(offset, true); offset += 4;
      const qx = view.getFloat32(offset, true); offset += 4;
      const qy = view.getFloat32(offset, true); offset += 4;
      const qz = view.getFloat32(offset, true); offset += 4;
      const qw = view.getFloat32(offset, true); offset += 4;

      boxes.push({
        trackId,
        agentFlag,
        category,
        center: [cx, cy, cz],
        size: [l, w, h],
        rotation: [qx, qy, qz, qw],
      });
    }

    frames.push({
      timeUs,
      agents: [
        { present: true, ...vehiclePose, points: lidarPoints },
        { present: true, ...dronePose, points: new Float32Array(0) },
      ],
      boxes,
    });
  }

  if (offset !== buffer.byteLength) {
    throw new Error(`Clip has ${buffer.byteLength - offset} trailing bytes`);
  }

  return {
    version: v2 ? 2 : 1,
    agents,
    origin: [ox, oy, oz],
    mapPositions: new Float32Array(mapPosList),
    mapColors: new Float32Array(mapColList),
    frames,
  };
}
