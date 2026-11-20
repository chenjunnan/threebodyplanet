import './evaluation.css';

import {
  ArcRotateCamera,
  Color3,
  DirectionalLight,
  Engine,
  Mesh,
  MeshBuilder,
  Scene,
  SceneLoader,
  StandardMaterial,
  Vector3,
  VertexData,
} from '@babylonjs/core';
import '@babylonjs/loaders/glTF';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

type EngineName = 'babylon' | 'three';
type EvaluationResult = {
  engine: EngineName;
  renderer: string;
  terrainVertices: number;
  terrainTriangles: number;
  directionalLights: number;
  assetLoaded: boolean;
  collisionCount: number;
  firstFrameMs: number;
  frameTimeP50Ms: number;
  frameTimeP95Ms: number;
  measuredFrames: number;
  heapUsedMiB: number | null;
};

declare global {
  interface Window { __ENGINE_EVALUATION__?: EvaluationResult; }
  interface Performance { memory?: { usedJSHeapSize: number }; }
}

const canvasElement = document.querySelector<HTMLCanvasElement>('#evaluation-canvas');
const outputElement = document.querySelector<HTMLElement>('#evaluation-result');
if (!canvasElement || !outputElement) throw new Error('evaluation DOM is incomplete');
const canvas: HTMLCanvasElement = canvasElement;
const output: HTMLElement = outputElement;

const resolution = 128;
const size = 220;
const positions: number[] = [];
const indices: number[] = [];
const terrainHeight = (x: number, z: number): number => (
  7 * Math.sin(x * 0.045) * Math.cos(z * 0.037)
  + 2.5 * Math.sin((x + z) * 0.13)
);
for (let row = 0; row <= resolution; row += 1) {
  for (let column = 0; column <= resolution; column += 1) {
    const x = size * (column / resolution - 0.5);
    const z = size * (row / resolution - 0.5);
    positions.push(x, terrainHeight(x, z), z);
  }
}
for (let row = 0; row < resolution; row += 1) {
  for (let column = 0; column < resolution; column += 1) {
    const first = row * (resolution + 1) + column;
    const next = first + resolution + 1;
    indices.push(first, next, first + 1, first + 1, next, next + 1);
  }
}

const percentile = (values: number[], fraction: number): number => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * fraction))] ?? 0;
};
const heapMiB = (): number | null => performance.memory
  ? performance.memory.usedJSHeapSize / (1024 * 1024) : null;

function finish(
  engine: EngineName,
  renderer: string,
  frames: number[],
  firstFrameMs: number,
  assetLoaded: boolean,
  collisionCount: number,
): void {
  const measured = frames.slice(60);
  const result: EvaluationResult = {
    engine,
    renderer,
    terrainVertices: positions.length / 3,
    terrainTriangles: indices.length / 3,
    directionalLights: 3,
    assetLoaded,
    collisionCount,
    firstFrameMs,
    frameTimeP50Ms: percentile(measured, 0.5),
    frameTimeP95Ms: percentile(measured, 0.95),
    measuredFrames: measured.length,
    heapUsedMiB: heapMiB(),
  };
  window.__ENGINE_EVALUATION__ = result;
  output.textContent = JSON.stringify(result, null, 2);
}

async function runBabylon(): Promise<void> {
  const started = performance.now();
  const engine = new Engine(canvas, true, { preserveDrawingBuffer: false, stencil: true });
  const scene = new Scene(engine);
  scene.clearColor.set(0.025, 0.04, 0.08, 1);
  const camera = new ArcRotateCamera('camera', -1.2, 1.05, 180, Vector3.Zero(), scene);
  camera.attachControl(canvas, true);
  camera.lowerRadiusLimit = 20;
  camera.upperRadiusLimit = 360;
  const colors = [new Color3(1, 0.72, 0.48), new Color3(0.62, 0.75, 1), new Color3(1, 0.35, 0.18)];
  const directions = [new Vector3(-1, -2, -1), new Vector3(1, -1, 0.3), new Vector3(0.2, -1, 1)];
  directions.forEach((direction, index) => {
    const light = new DirectionalLight(`star-${index}`, direction, scene);
    light.diffuse = colors[index] ?? Color3.White();
    light.intensity = 0.9 / (index + 1);
  });
  const terrain = new Mesh('terrain', scene);
  const normals: number[] = [];
  VertexData.ComputeNormals(positions, indices, normals);
  const vertexData = new VertexData();
  vertexData.positions = positions;
  vertexData.indices = indices;
  vertexData.normals = normals;
  vertexData.applyToMesh(terrain);
  terrain.checkCollisions = true;
  const material = new StandardMaterial('terrain-material', scene);
  material.diffuseColor = new Color3(0.25, 0.48, 0.18);
  terrain.material = material;
  const collider = MeshBuilder.CreateSphere('collider', { diameter: 4, segments: 16 }, scene);
  collider.position.set(-80, 35, 0);
  collider.checkCollisions = true;
  let velocity = new Vector3(18, 0, 9);
  let collisions = 0;
  let assetLoaded = false;
  try {
    await SceneLoader.ImportMeshAsync('', '/game/', 'eval-marker.gltf', scene);
    assetLoaded = true;
  } catch (error) {
    output.textContent = `Babylon glTF load failed: ${String(error)}`;
  }
  const frames: number[] = [];
  let last = performance.now();
  let firstFrame = 0;
  engine.runRenderLoop(() => {
    const now = performance.now();
    const dt = Math.min(0.05, (now - last) / 1000);
    frames.push(now - last);
    last = now;
    if (!firstFrame) firstFrame = now - started;
    velocity.y -= 16 * dt;
    collider.position.addInPlace(velocity.scale(dt));
    const ground = terrainHeight(collider.position.x, collider.position.z) + 2;
    if (collider.position.y < ground) {
      collider.position.y = ground;
      velocity.y = Math.abs(velocity.y) * 0.72;
      collisions += 1;
    }
    if (Math.abs(collider.position.x) > 100) velocity.x *= -1;
    if (Math.abs(collider.position.z) > 100) velocity.z *= -1;
    scene.render();
    if (frames.length === 420) {
      finish('babylon', engine.getGlInfo().renderer, frames, firstFrame, assetLoaded, collisions);
    }
  });
  window.addEventListener('resize', () => engine.resize());
}

async function runThree(): Promise<void> {
  const started = performance.now();
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x07101f);
  const camera = new THREE.PerspectiveCamera(60, canvas.clientWidth / canvas.clientHeight, 0.1, 1000);
  camera.position.set(120, 100, 120);
  const controls = new OrbitControls(camera, canvas);
  const colors = [0xffb878, 0x9ebfff, 0xff5930];
  const directions = [new THREE.Vector3(-1, -2, -1), new THREE.Vector3(1, -1, 0.3), new THREE.Vector3(0.2, -1, 1)];
  directions.forEach((direction, index) => {
    const light = new THREE.DirectionalLight(colors[index], 2.2 / (index + 1));
    light.position.copy(direction.multiplyScalar(-100));
    scene.add(light);
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  scene.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: 0x3d7a2e })));
  const collider = new THREE.Mesh(
    new THREE.SphereGeometry(2, 16, 12), new THREE.MeshStandardMaterial({ color: 0xffcc44 }),
  );
  collider.position.set(-80, 35, 0);
  scene.add(collider);
  let velocity = new THREE.Vector3(18, 0, 9);
  let collisions = 0;
  let assetLoaded = false;
  try {
    const asset = await new GLTFLoader().loadAsync('/game/eval-marker.gltf');
    scene.add(asset.scene);
    assetLoaded = true;
  } catch (error) {
    output.textContent = `Three glTF load failed: ${String(error)}`;
  }
  const frames: number[] = [];
  let last = performance.now();
  let firstFrame = 0;
  const render = (now: number): void => {
    const dt = Math.min(0.05, (now - last) / 1000);
    frames.push(now - last);
    last = now;
    if (!firstFrame) firstFrame = now - started;
    velocity.y -= 16 * dt;
    collider.position.addScaledVector(velocity, dt);
    const ground = terrainHeight(collider.position.x, collider.position.z) + 2;
    if (collider.position.y < ground) {
      collider.position.y = ground;
      velocity.y = Math.abs(velocity.y) * 0.72;
      collisions += 1;
    }
    if (Math.abs(collider.position.x) > 100) velocity.x *= -1;
    if (Math.abs(collider.position.z) > 100) velocity.z *= -1;
    controls.update();
    renderer.render(scene, camera);
    if (frames.length === 420) {
      finish('three', renderer.info.render.calls.toString(), frames, firstFrame, assetLoaded, collisions);
    } else {
      requestAnimationFrame(render);
    }
  };
  requestAnimationFrame(render);
}

const selected = new URLSearchParams(location.search).get('engine') === 'three'
  ? 'three' : 'babylon';
void (selected === 'babylon' ? runBabylon() : runThree());
