import * as THREE from "three";

/** Far plane of a frustum, in metres. Long enough to read in orbit. */
export const FRUSTUM_DEPTH = 12;
export const FRUSTUM_TEXTURE = 360;

const textures = new Map<string, { bitmap: ImageBitmap; texture: THREE.Texture }>();

export function imagePlaneGeometry(fovY: number, aspect: number, depth = FRUSTUM_DEPTH): THREE.BufferGeometry {
  const halfHeight = Math.tan(fovY / 2) * depth;
  const halfWidth = halfHeight * aspect;
  // Sensor frame is OpenCV: +X right, +Y down, +Z forward. Image origin is the top left.
  const positions = new Float32Array([
    -halfWidth, -halfHeight, depth,
    halfWidth, -halfHeight, depth,
    halfWidth, halfHeight, depth,
    -halfWidth, halfHeight, depth,
  ]);
  const uvs = new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
  geometry.setIndex([0, 1, 2, 0, 2, 3]);
  return geometry;
}

export function createImagePlane(fovY: number, aspect: number): THREE.Mesh {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    opacity: 0.85,
    depthWrite: false,
    side: THREE.DoubleSide,
    toneMapped: false,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1,
  });
  const mesh = new THREE.Mesh(imagePlaneGeometry(fovY, aspect), material);
  mesh.frustumCulled = false;
  mesh.renderOrder = 3;
  return mesh;
}

export function textureForBitmap(key: string, bitmap: ImageBitmap): THREE.Texture {
  const existing = textures.get(key);
  if (existing && existing.bitmap === bitmap) {
    return existing.texture;
  }
  existing?.texture.dispose();
  const texture = new THREE.Texture(bitmap);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.flipY = false;
  texture.generateMipmaps = false;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  textures.set(key, { bitmap, texture });
  while (textures.size > 48) {
    const oldest = textures.keys().next().value;
    if (oldest === undefined) {
      break;
    }
    textures.get(oldest)?.texture.dispose();
    textures.delete(oldest);
  }
  return texture;
}

export function disposeImageTextures(): void {
  for (const item of textures.values()) {
    item.texture.dispose();
  }
  textures.clear();
}
