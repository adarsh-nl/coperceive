import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

export const BLOOM_LAYER = 1;

const mixShader = {
  uniforms: {
    baseTexture: { value: null as THREE.Texture | null },
    bloomTexture: { value: null as THREE.Texture | null },
  },
  vertexShader: `
    varying vec2 vUv;
    void main() {
      vUv = uv;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    uniform sampler2D baseTexture;
    uniform sampler2D bloomTexture;
    varying vec2 vUv;
    void main() {
      vec4 base = texture2D(baseTexture, vUv);
      vec4 bloom = texture2D(bloomTexture, vUv);
      gl_FragColor = vec4(base.rgb + bloom.rgb, 1.0);
    }
  `,
};

export type BloomRig = {
  render: () => void;
  setSize: (width: number, height: number, ratio: number) => void;
  dispose: () => void;
};

export function createBloom(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.PerspectiveCamera): BloomRig {
  const bloomPass = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.42, 0.38, 0.22);
  const bloomComposer = new EffectComposer(renderer);
  bloomComposer.renderToScreen = false;
  bloomComposer.addPass(new RenderPass(scene, camera));
  bloomComposer.addPass(bloomPass);

  const mixPass = new ShaderPass(mixShader, "baseTexture");
  const finalComposer = new EffectComposer(renderer);
  finalComposer.addPass(new RenderPass(scene, camera));
  finalComposer.addPass(mixPass);

  const black = new THREE.Color(0x000000);
  const bloomUniform = mixPass.uniforms["bloomTexture"];

  function render() {
    const mask = camera.layers.mask;
    const background = scene.background;
    camera.layers.set(BLOOM_LAYER);
    scene.background = black;
    bloomComposer.render();
    scene.background = background;
    camera.layers.mask = mask;
    const glow = bloomPass.renderTargetsHorizontal[0]?.texture ?? null;
    if (bloomUniform) {
      bloomUniform.value = glow;
    }
    finalComposer.render();
  }

  function setSize(width: number, height: number, ratio: number) {
    const pixelRatio = ratio > 0 ? ratio : 1;
    bloomComposer.setPixelRatio(pixelRatio);
    finalComposer.setPixelRatio(pixelRatio);
    bloomComposer.setSize(width, height);
    finalComposer.setSize(width, height);
    bloomPass.resolution.set(Math.max(1, width * pixelRatio), Math.max(1, height * pixelRatio));
  }

  function dispose() {
    bloomComposer.dispose();
    finalComposer.dispose();
    bloomPass.dispose();
  }

  return { render, setSize, dispose };
}

export function enableBloom(object: THREE.Object3D) {
  object.layers.enable(BLOOM_LAYER);
}
