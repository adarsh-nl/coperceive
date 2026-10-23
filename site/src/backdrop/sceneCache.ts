import { createBackdropScene, type BackdropScene } from "./scene";

let scene: BackdropScene | null = null;

export function getBackdrop(): BackdropScene {
  scene ??= createBackdropScene(42);
  return scene;
}
