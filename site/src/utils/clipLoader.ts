import type { ClipManifest, FrameData, ParsedClip, BoxRecord } from "../types/clip";
import { tokens } from "../tokens";

export async function loadClip(
  manifestUrl: string = `${import.meta.env.BASE_URL}hero/manifest.json`,
  binUrl: string = `${import.meta.env.BASE_URL}hero/clip.bin`,
  signal?: AbortSignal
): Promise<ParsedClip> {
  const [manifestRes, binRes] = await Promise.all([
    fetch(manifestUrl, { signal }),
    fetch(binUrl, { signal }),
  ]);

  if (!manifestRes.ok) {
    throw new Error(`Failed to load manifest: ${manifestRes.status}`);
  }
  if (!binRes.ok) {
    throw new Error(`Failed to load binary clip: ${binRes.status}`);
  }

  const manifest: ClipManifest = await manifestRes.json();
  const buffer = await binRes.arrayBuffer();
  const view = new DataView(buffer);

  // Check magic
  const magic = String.fromCharCode(
    view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3),
    view.getUint8(4), view.getUint8(5), view.getUint8(6), view.getUint8(7)
  );
  if (magic !== "CPCVCLIP") {
    throw new Error(`Invalid clip magic header: ${magic}`);
  }

  let offset = 8;
  offset += 2; // version
  const ox = view.getFloat32(offset, true); offset += 4;
  const oy = view.getFloat32(offset, true); offset += 4;
  const oz = view.getFloat32(offset, true); offset += 4;
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

  const mapPositions = new Float32Array(mapPosList);
  const mapColors = new Float32Array(mapColList);

  // Unpack frames
  const frames: FrameData[] = [];
  for (let f = 0; f < numFrames; f++) {
    // uint64 time_us
    const low = view.getUint32(offset, true);
    const high = view.getUint32(offset + 4, true);
    const timeUs = high * 4294967296 + low;
    offset += 8;

    // Vehicle pose (tx, ty, tz, qx, qy, qz, qw)
    const vtx = view.getFloat32(offset, true); offset += 4;
    const vty = view.getFloat32(offset, true); offset += 4;
    const vtz = view.getFloat32(offset, true); offset += 4;
    const vqx = view.getFloat32(offset, true); offset += 4;
    const vqy = view.getFloat32(offset, true); offset += 4;
    const vqz = view.getFloat32(offset, true); offset += 4;
    const vqw = view.getFloat32(offset, true); offset += 4;

    // Drone pose (tx, ty, tz, qx, qy, qz, qw)
    const dtx = view.getFloat32(offset, true); offset += 4;
    const dty = view.getFloat32(offset, true); offset += 4;
    const dtz = view.getFloat32(offset, true); offset += 4;
    const dqx = view.getFloat32(offset, true); offset += 4;
    const dqy = view.getFloat32(offset, true); offset += 4;
    const dqz = view.getFloat32(offset, true); offset += 4;
    const dqw = view.getFloat32(offset, true); offset += 4;

    // LiDAR points
    const nPts = view.getUint16(offset, true); offset += 2;
    const pts = new Float32Array(nPts * 3);
    for (let p = 0; p < nPts; p++) {
      const dx = view.getInt16(offset, true); offset += 2;
      const dy = view.getInt16(offset, true); offset += 2;
      const dz = view.getInt16(offset, true); offset += 2;
      pts[p * 3] = ox + dx * 0.01;
      pts[p * 3 + 1] = oy + dy * 0.01;
      pts[p * 3 + 2] = oz + dz * 0.01;
    }

    // Boxes
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
      vehiclePose: {
        position: [vtx, vty, vtz],
        quaternion: [vqx, vqy, vqz, vqw],
      },
      dronePose: {
        position: [dtx, dty, dtz],
        quaternion: [dqx, dqy, dqz, dqw],
      },
      lidarPoints: pts,
      boxes,
    });
  }

  return {
    manifest,
    origin: [ox, oy, oz],
    mapPositions,
    mapColors,
    frames,
  };
}
