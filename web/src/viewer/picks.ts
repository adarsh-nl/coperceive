import * as THREE from "three";
import { toPick, type PickRecord } from "./inspect";
import type { FrameBox } from "./types";

const material = new THREE.MeshBasicMaterial({
  transparent: true,
  opacity: 0,
  depthWrite: false,
  colorWrite: false,
});

export function syncPicks(group: THREE.Group, boxes: readonly FrameBox[], shown: (box: FrameBox) => boolean): void {
  const picks: PickRecord[] = [];
  boxes.forEach((box, index) => {
    if (!shown(box)) {
      return;
    }
    const pick = toPick(box, index);
    if (pick) {
      picks.push(pick);
    }
  });
  while (group.children.length > picks.length) {
    const child = group.children.pop();
    if (child instanceof THREE.Mesh) {
      child.geometry.dispose();
    }
  }
  picks.forEach((pick, index) => {
    let mesh = group.children[index];
    if (!(mesh instanceof THREE.Mesh)) {
      mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material);
      group.add(mesh);
    }
    mesh.scale.set(pick.size[0], pick.size[1], pick.size[2]);
    mesh.position.set(pick.center[0], pick.center[1], pick.center[2]);
    const box = boxes[pick.index];
    if (box?.pose) {
      mesh.quaternion.set(box.pose.qx, box.pose.qy, box.pose.qz, box.pose.qw);
    }
    mesh.userData.pick = pick;
    mesh.visible = true;
  });
}

export function pickFromObject(object: THREE.Object3D): PickRecord | null {
  const value: unknown = object.userData.pick;
  if (!value || typeof value !== "object") {
    return null;
  }
  return value as PickRecord;
}
