import * as THREE from "three";
import type { Pose } from "./types";

export type Pixel = { u: number; v: number };

export type Pinhole = {
  pose: Pose;
  fx: number;
  fy: number;
  cx: number;
  cy: number;
  imageWidth: number | null;
  imageHeight: number | null;
};

const scratch = new THREE.Vector3();
const inverse = new THREE.Quaternion();

export function projectToImage(
  camera: Pinhole,
  x: number,
  y: number,
  z: number,
): (Pixel & { depth: number }) | null {
  inverse.set(camera.pose.qx, camera.pose.qy, camera.pose.qz, camera.pose.qw).invert();
  scratch.set(x - camera.pose.tx, y - camera.pose.ty, z - camera.pose.tz).applyQuaternion(inverse);
  if (scratch.z <= 0) {
    return null;
  }
  return {
    u: (camera.fx * scratch.x) / scratch.z + camera.cx,
    v: (camera.fy * scratch.y) / scratch.z + camera.cy,
    depth: scratch.z,
  };
}
