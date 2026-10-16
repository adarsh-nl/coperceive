import * as THREE from "three";
import type { Pose } from "./types";

export type Viewpoint = "orbit" | "bird" | "chase" | "drone" | "camera";

export type Anchor = { x: number; y: number; z: number };

export type CameraGoal = {
  position: THREE.Vector3;
  target: THREE.Vector3;
  up: THREE.Vector3;
  fov: number;
  near: number;
  far: number;
};

const WORLD_UP = () => new THREE.Vector3(0, 0, 1);
const NORTH_UP = () => new THREE.Vector3(0, 1, 0);

export function bodyForward(pose: Pose): THREE.Vector3 {
  const rotation = new THREE.Quaternion(pose.qx, pose.qy, pose.qz, pose.qw);
  return new THREE.Vector3(1, 0, 0).applyQuaternion(rotation).normalize();
}

export function chaseGoal(vehicle: Pose): CameraGoal {
  const forward = bodyForward(vehicle);
  const origin = new THREE.Vector3(vehicle.tx, vehicle.ty, vehicle.tz);
  const up = WORLD_UP();
  return {
    position: origin.clone().addScaledVector(forward, -12).addScaledVector(up, 5),
    target: origin.clone().addScaledVector(forward, 8).addScaledVector(up, 1),
    up,
    fov: 55,
    near: 0.1,
    far: 5000,
  };
}

export function birdGoal(vehicle: Pose): CameraGoal {
  const origin = new THREE.Vector3(vehicle.tx, vehicle.ty, vehicle.tz);
  return {
    position: origin.clone().add(new THREE.Vector3(0, 0, 48)),
    target: origin.clone(),
    up: NORTH_UP(),
    fov: 50,
    near: 0.1,
    far: 5000,
  };
}

export function droneGoal(drone: Pose, vehicle: Pose): CameraGoal {
  const forward = bodyForward(drone);
  const origin = new THREE.Vector3(drone.tx, drone.ty, drone.tz);
  const up = WORLD_UP();
  return {
    position: origin.clone().addScaledVector(forward, -2).addScaledVector(up, 1),
    target: new THREE.Vector3(vehicle.tx, vehicle.ty, vehicle.tz),
    up,
    fov: 55,
    near: 0.1,
    far: 5000,
  };
}

export function cameraGoal(pose: Pose, fovYRad: number): CameraGoal {
  const rotation = new THREE.Quaternion(pose.qx, pose.qy, pose.qz, pose.qw);
  // Sensor +Z is forward. On Griffin Town03-000 the vehicle front camera's +Y
  // points down (world (0, -0.005, -1)), so camera.up follows sensor −Y.
  const forward = new THREE.Vector3(0, 0, 1).applyQuaternion(rotation).normalize();
  const up = new THREE.Vector3(0, -1, 0).applyQuaternion(rotation).normalize();
  const origin = new THREE.Vector3(pose.tx, pose.ty, pose.tz);
  const fov = (fovYRad * 180) / Math.PI;
  return {
    position: origin,
    target: origin.clone().addScaledVector(forward, 12),
    up,
    fov: Number.isFinite(fov) && fov > 1 && fov < 170 ? fov : 55,
    near: 0.05,
    far: 5000,
  };
}

export function orbitGoal(positions: ArrayLike<number>, anchor: Anchor | null): CameraGoal {
  const origin = anchor ?? medianAnchor(positions);
  const kept: THREE.Vector3[] = [new THREE.Vector3(origin.x, origin.y, origin.z)];
  const radius = 80;
  for (let index = 0; index < positions.length; index += 3) {
    const x = positions[index] ?? 0;
    const y = positions[index + 1] ?? 0;
    const z = positions[index + 2] ?? 0;
    const dx = x - origin.x;
    const dy = y - origin.y;
    if (dx * dx + dy * dy <= radius * radius) {
      kept.push(new THREE.Vector3(x, y, z));
    }
  }
  const box = new THREE.Box3().setFromPoints(kept);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const span = Math.max(size.length() * 0.55, 18);
  return {
    position: new THREE.Vector3(center.x + span, center.y - span, center.z + span * 0.75),
    target: center,
    up: WORLD_UP(),
    fov: 55,
    near: 0.1,
    far: Math.max(5000, span * 40),
  };
}

function medianAnchor(positions: ArrayLike<number>): Anchor {
  if (positions.length < 3) {
    return { x: 0, y: 0, z: 0 };
  }
  const xs: number[] = [];
  const ys: number[] = [];
  const zs: number[] = [];
  for (let index = 0; index < positions.length; index += 3) {
    xs.push(positions[index] ?? 0);
    ys.push(positions[index + 1] ?? 0);
    zs.push(positions[index + 2] ?? 0);
  }
  return { x: median(xs), y: median(ys), z: median(zs) };
}

function median(values: number[]): number {
  const ordered = [...values].sort((a, b) => a - b);
  const mid = Math.floor(ordered.length / 2);
  if (ordered.length % 2 === 1) {
    return ordered[mid] ?? 0;
  }
  return ((ordered[mid - 1] ?? 0) + (ordered[mid] ?? 0)) / 2;
}

const SMOOTH_SECONDS = 0.18;

export function applyGoal(camera: THREE.PerspectiveCamera, target: THREE.Vector3, goal: CameraGoal) {
  camera.position.copy(goal.position);
  target.copy(goal.target);
  camera.up.copy(goal.up);
  camera.fov = goal.fov;
  camera.near = goal.near;
  camera.far = goal.far;
  camera.lookAt(target);
  camera.updateProjectionMatrix();
}

export function approachGoal(
  camera: THREE.PerspectiveCamera,
  target: THREE.Vector3,
  goal: CameraGoal,
  dtSeconds: number,
): boolean {
  const step = 1 - Math.exp(-Math.min(dtSeconds, 0.05) / SMOOTH_SECONDS);
  camera.position.lerp(goal.position, step);
  target.lerp(goal.target, step);
  camera.up.lerp(goal.up, step);
  if (camera.up.lengthSq() < 1e-8) {
    camera.up.copy(goal.up);
  } else {
    camera.up.normalize();
  }
  camera.fov += (goal.fov - camera.fov) * step;
  camera.near += (goal.near - camera.near) * step;
  camera.far += (goal.far - camera.far) * step;
  camera.lookAt(target);
  camera.updateProjectionMatrix();
  return (
    camera.position.distanceTo(goal.position) < 0.08 &&
    target.distanceTo(goal.target) < 0.08 &&
    Math.abs(camera.fov - goal.fov) < 0.4
  );
}
