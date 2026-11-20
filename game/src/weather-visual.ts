import { Color4, Vector3 } from '@babylonjs/core/Maths/math';
import { ParticleSystem } from '@babylonjs/core/Particles/particleSystem';
import type { Scene } from '@babylonjs/core/scene';
import { Texture } from '@babylonjs/core/Materials/Textures/texture';
import { Mesh } from '@babylonjs/core/Meshes/mesh';
import { MeshBuilder } from '@babylonjs/core/Meshes/meshBuilder';
import { StandardMaterial } from '@babylonjs/core/Materials/standardMaterial';
import { Color3 } from '@babylonjs/core/Maths/math.color';

import type { EnvironmentPoint } from './api';
import { environmentField, weatherState } from './environment';

const PARTICLE_TEXTURE = (
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"8\" height=\"8\">' +
    '<circle cx=\"4\" cy=\"4\" r=\"3\" fill=\"white\"/></svg>',
  )
);

export class WeatherVisual {
  private readonly precipitation: ParticleSystem;
  private readonly clouds: Mesh[] = [];
  private windEastMps = 0;
  private windNorthMps = 0;
  private cloudFraction = 0;

  constructor(scene: Scene) {
    this.precipitation = new ParticleSystem('archived-precipitation', 1600, scene);
    this.precipitation.particleTexture = new Texture(
      PARTICLE_TEXTURE,
      scene,
      false,
      false,
    );
    this.precipitation.emitter = new Vector3(0, 20, 0);
    this.precipitation.minEmitBox = new Vector3(-18, 0, -18);
    this.precipitation.maxEmitBox = new Vector3(18, 4, 18);
    this.precipitation.minLifeTime = 0.5;
    this.precipitation.maxLifeTime = 2;
    this.precipitation.minSize = 0.03;
    this.precipitation.maxSize = 0.08;
    this.precipitation.gravity = new Vector3(0, -18, 0);
    this.precipitation.emitRate = 0;
    this.precipitation.start();
    const cloudMaterial = new StandardMaterial('cloud-layer-material', scene);
    cloudMaterial.diffuseColor = new Color3(0.85, 0.88, 0.92);
    cloudMaterial.emissiveColor = new Color3(0.08, 0.08, 0.09);
    cloudMaterial.backFaceCulling = false;
    cloudMaterial.alpha = 0.45;
    for (let index = 0; index < 8; index += 1) {
      const cloud = MeshBuilder.CreateDisc(
        'parameterized-cloud-' + index,
        { radius: 18 + index % 3 * 7, tessellation: 32 },
        scene,
      );
      cloud.rotation.x = Math.PI / 2;
      cloud.material = cloudMaterial;
      cloud.isVisible = false;
      this.clouds.push(cloud);
    }
  }

  update(environment: EnvironmentPoint, playerPosition: Vector3): void {
    const east = environmentField(environment, 'eastward_wind').value;
    const north = environmentField(environment, 'northward_wind').value;
    this.windEastMps = east ?? 0;
    this.windNorthMps = north ?? 0;
    const precipitation = environmentField(environment, 'precipitation_rate').value;
    const cloud = environmentField(environment, 'cloud_fraction').value ?? 0;
    this.cloudFraction = Math.max(0, Math.min(1, cloud));
    const weather = weatherState(environment);
    this.precipitation.emitter = new Vector3(
      playerPosition.x,
      playerPosition.y + 20,
      playerPosition.z,
    );
    if (precipitation === null || precipitation <= 0 || weather.quality === 'UNAVAILABLE') {
      this.precipitation.emitRate = 0;
      return;
    }
    // Visual particle count is an exposure mapping only. The archived SI rate,
    // phase, and region remain authoritative.
    this.precipitation.emitRate = Math.min(
      1400,
      Math.max(20, precipitation * 1.5e6 * (0.4 + cloud)),
    );
    const snow = weather.phase === 'SNOW';
    this.precipitation.color1 = snow
      ? new Color4(0.9, 0.95, 1, 0.9)
      : new Color4(0.55, 0.72, 1, 0.75);
    this.precipitation.color2 = this.precipitation.color1;
    this.precipitation.minSize = snow ? 0.06 : 0.025;
    this.precipitation.maxSize = snow ? 0.16 : 0.055;
    this.precipitation.minLifeTime = snow ? 2 : 0.5;
    this.precipitation.maxLifeTime = snow ? 5 : 1.5;
    this.precipitation.gravity = new Vector3(
      this.windEastMps * 0.25,
      snow ? -2.5 : -22,
      this.windNorthMps * 0.25,
    );
  }

  tick(dt: number, playerPosition: Vector3): void {
    for (let index = 0; index < this.clouds.length; index += 1) {
      const cloud = this.clouds[index]!;
      cloud.isVisible = index / this.clouds.length < this.cloudFraction;
      if (!cloud.isVisible) continue;
      if (cloud.position.lengthSquared() === 0) {
        cloud.position.set(
          playerPosition.x + (index % 4 - 1.5) * 28,
          playerPosition.y + 55 + index % 3 * 8,
          playerPosition.z + (Math.floor(index / 4) - 0.5) * 38,
        );
      }
      cloud.position.x += this.windEastMps * dt * 0.08;
      cloud.position.z += this.windNorthMps * dt * 0.08;
      if (Math.abs(cloud.position.x - playerPosition.x) > 100) {
        cloud.position.x = playerPosition.x - Math.sign(this.windEastMps || 1) * 90;
      }
      if (Math.abs(cloud.position.z - playerPosition.z) > 100) {
        cloud.position.z = playerPosition.z - Math.sign(this.windNorthMps || 1) * 90;
      }
    }
  }

  dispose(): void {
    this.precipitation.dispose();
    for (const cloud of this.clouds) cloud.dispose(false, true);
  }
}
