import { Color3 } from '@babylonjs/core/Maths/math.color';
import { Mesh } from '@babylonjs/core/Meshes/mesh';
import { VertexData } from '@babylonjs/core/Meshes/mesh.vertexData';
import { StandardMaterial } from '@babylonjs/core/Materials/standardMaterial';
import type { Scene } from '@babylonjs/core/scene';

import type { Scenario } from './api';
import {
  cross,
  floatingOriginRequired,
  geodeticUnit,
  length,
  normalize,
  terrainHeight,
  type Vec3,
} from './physics';

export interface SurfaceStyle {
  liquidWater: number;
  ice: number;
  magma: number;
  uncertain: boolean;
}

export interface TerrainLocation {
  latitudeDeg: number;
  longitudeDeg: number;
  elevationM: number;
}

export interface RenderLocation extends TerrainLocation {
  eastM: number;
  northM: number;
  groundY: number;
  rebased: boolean;
}

function cubeFaceKey(normal: Vec3, level: number): string {
  const [x, y, z] = normal;
  const ax = Math.abs(x), ay = Math.abs(y), az = Math.abs(z);
  let face: string;
  let u: number;
  let v: number;
  if (ax >= ay && ax >= az) {
    face = x >= 0 ? 'px' : 'nx';
    u = (x >= 0 ? -z : z) / ax;
    v = y / ax;
  } else if (ay >= az) {
    face = y >= 0 ? 'py' : 'ny';
    u = x / ay;
    v = (y >= 0 ? -z : z) / ay;
  } else {
    face = z >= 0 ? 'pz' : 'nz';
    u = (z >= 0 ? x : -x) / az;
    v = y / az;
  }
  const cells = 2 ** level;
  const column = Math.min(cells - 1, Math.max(0, Math.floor((u + 1) * 0.5 * cells)));
  const row = Math.min(cells - 1, Math.max(0, Math.floor((v + 1) * 0.5 * cells)));
  return face + '/' + level + '/' + column + '/' + row;
}

export class TerrainManager {
  private meshes: Mesh[] = [];
  private anchorLatitudeDeg: number;
  private anchorLongitudeDeg: number;
  private anchorElevationM: number;
  private anchorNormal: Vec3;
  private anchorEast: Vec3;
  private anchorNorth: Vec3;
  private style: SurfaceStyle = {
    liquidWater: 0,
    ice: 0,
    magma: 0,
    uncertain: true,
  };
  private surfaceMaterial: StandardMaterial | null = null;

  constructor(
    private readonly scene: Scene,
    private readonly scenario: Scenario,
    latitudeDeg: number,
    longitudeDeg: number,
  ) {
    this.anchorLatitudeDeg = latitudeDeg;
    this.anchorLongitudeDeg = longitudeDeg;
    this.anchorElevationM = this.elevation(latitudeDeg, longitudeDeg);
    this.anchorNormal = geodeticUnit(latitudeDeg, longitudeDeg);
    this.anchorEast = this.makeEast(this.anchorNormal);
    this.anchorNorth = normalize(cross(this.anchorNormal, this.anchorEast));
    this.rebuild();
  }

  elevation(latitudeDeg: number, longitudeDeg: number): number {
    return terrainHeight(
      geodeticUnit(latitudeDeg, longitudeDeg),
      this.scenario.terrain_modes,
      this.scenario.terrain_model,
      this.scenario.parameters.relief_m,
      this.scenario.parameters.terrain_shape_exponent,
    );
  }

  locate(latitudeDeg: number, longitudeDeg: number): RenderLocation {
    const radius = this.scenario.radius_m;
    const latitudeRad = latitudeDeg * Math.PI / 180;
    const eastM = (
      (longitudeDeg - this.anchorLongitudeDeg) * Math.PI / 180
      * radius * Math.max(1e-5, Math.cos(latitudeRad))
    );
    const northM = (
      (latitudeDeg - this.anchorLatitudeDeg) * Math.PI / 180 * radius
    );
    const elevationM = this.elevation(latitudeDeg, longitudeDeg);
    if (floatingOriginRequired(eastM, northM)) {
      this.anchorLatitudeDeg = latitudeDeg;
      this.anchorLongitudeDeg = longitudeDeg;
      this.anchorElevationM = elevationM;
      this.anchorNormal = geodeticUnit(latitudeDeg, longitudeDeg);
      this.anchorEast = this.makeEast(this.anchorNormal);
      this.anchorNorth = normalize(cross(this.anchorNormal, this.anchorEast));
      this.rebuild();
      return {
        latitudeDeg,
        longitudeDeg,
        elevationM,
        eastM: 0,
        northM: 0,
        groundY: 0,
        rebased: true,
      };
    }
    return {
      latitudeDeg,
      longitudeDeg,
      elevationM,
      eastM,
      northM,
      groundY: (
        elevationM - this.anchorElevationM
        - (eastM * eastM + northM * northM) / (2 * radius)
      ),
      rebased: false,
    };
  }

  setSurfaceStyle(style: SurfaceStyle): void {
    this.style = style;
    this.applyMaterials();
  }

  dispose(): void {
    for (const mesh of this.meshes) mesh.dispose(false, true);
    this.meshes = [];
    this.surfaceMaterial?.dispose();
    this.surfaceMaterial = null;
  }

  get meshCount(): number {
    return this.meshes.length;
  }

  get tileKeys(): string[] {
    return this.meshes.map((mesh) => String(mesh.metadata?.cubeSphereTile ?? ''));
  }

  private makeEast(up: Vec3): Vec3 {
    let east = cross([0, 0, 1], up);
    if (length(east) < 1e-9) east = [0, 1, 0];
    return normalize(east);
  }

  private rebuild(): void {
    this.dispose();
    // Level 12 is the playable 20x20 km center. A lower-resolution outer ring
    // supplies visible continuity while the floating origin follows the player.
    for (let row = -2; row <= 2; row += 1) {
      for (let column = -2; column <= 2; column += 1) {
        this.meshes.push(this.createTile(column, row, 4000, 24, 12));
      }
    }
    for (let row = -4; row <= 4; row += 1) {
      for (let column = -4; column <= 4; column += 1) {
        if (Math.abs(row) <= 2 && Math.abs(column) <= 2) continue;
        this.meshes.push(this.createTile(column, row, 4000, 8, 11));
      }
    }
    this.applyMaterials();
  }

  private normalAt(eastM: number, northM: number): Vec3 {
    const inverseRadius = 1 / this.scenario.radius_m;
    return normalize([
      this.anchorNormal[0]
        + this.anchorEast[0] * eastM * inverseRadius
        + this.anchorNorth[0] * northM * inverseRadius,
      this.anchorNormal[1]
        + this.anchorEast[1] * eastM * inverseRadius
        + this.anchorNorth[1] * northM * inverseRadius,
      this.anchorNormal[2]
        + this.anchorEast[2] * eastM * inverseRadius
        + this.anchorNorth[2] * northM * inverseRadius,
    ]);
  }

  private createTile(
    column: number,
    row: number,
    sizeM: number,
    resolution: number,
    level: number,
  ): Mesh {
    const positions: number[] = [];
    const indices: number[] = [];
    const colors: number[] = [];
    const wetMask: boolean[] = [];
    for (let z = 0; z <= resolution; z += 1) {
      for (let x = 0; x <= resolution; x += 1) {
        const eastM = (column + x / resolution) * sizeM;
        const northM = (row + z / resolution) * sizeM;
        const normal = this.normalAt(eastM, northM);
        const latitudeDeg = Math.asin(normal[2]) * 180 / Math.PI;
        const longitudeDeg = Math.atan2(normal[1], normal[0]) * 180 / Math.PI;
        const elevationM = this.elevation(latitudeDeg, longitudeDeg);
        positions.push(
          eastM,
          elevationM - this.anchorElevationM
            - (eastM * eastM + northM * northM) / (2 * this.scenario.radius_m),
          northM,
        );
        const wet = elevationM < this.scenario.sea_level_m;
        wetMask.push(wet);
        const snowy = !wet && elevationM > this.scenario.parameters.relief_m * 0.45;
        colors.push(...(wet
          ? [0.02, 0.17, 0.28, 1]
          : snowy
            ? [0.85, 0.88, 0.9, 1]
            : elevationM > 0
              ? [0.3, 0.23, 0.14, 1]
              : [0.16, 0.25, 0.11, 1]));
      }
    }
    for (let z = 0; z < resolution; z += 1) {
      for (let x = 0; x < resolution; x += 1) {
        const a = z * (resolution + 1) + x;
        indices.push(a, a + resolution + 1, a + 1);
        indices.push(a + 1, a + resolution + 1, a + resolution + 2);
        if (
          wetMask[a]
          && wetMask[a + 1]
          && wetMask[a + resolution + 1]
          && wetMask[a + resolution + 2]
        ) {
          const waterStart = positions.length / 3;
          for (const [waterX, waterZ] of [
            [x, z], [x, z + 1], [x + 1, z], [x + 1, z + 1],
          ] as const) {
            const waterEast = (column + waterX / resolution) * sizeM;
            const waterNorth = (row + waterZ / resolution) * sizeM;
            positions.push(
              waterEast,
              this.scenario.sea_level_m - this.anchorElevationM
                - (waterEast * waterEast + waterNorth * waterNorth)
                  / (2 * this.scenario.radius_m)
                + 0.2,
              waterNorth,
            );
            colors.push(0.03, 0.24, 0.38, 1);
          }
          indices.push(
            waterStart, waterStart + 1, waterStart + 2,
            waterStart + 2, waterStart + 1, waterStart + 3,
          );
        }
      }
    }
    const center = this.normalAt((column + 0.5) * sizeM, (row + 0.5) * sizeM);
    const mesh = new Mesh('terrain-' + cubeFaceKey(center, level), this.scene);
    const normals: number[] = [];
    VertexData.ComputeNormals(positions, indices, normals);
    const data = new VertexData();
    data.positions = positions;
    data.indices = indices;
    data.normals = normals;
    data.colors = colors;
    data.applyToMesh(mesh);
    mesh.metadata = {
      cubeSphereTile: cubeFaceKey(center, level),
      lodLevel: level,
      parameterizedTerrain: true,
    };
    return mesh;
  }

  private applyMaterials(): void {
    this.surfaceMaterial?.dispose();
    const material = new StandardMaterial('surface-state', this.scene);
    // Height vertices use the scientific right-handed east/up/north frame,
    // while Babylon's default scene is left-handed. Render both windings so
    // the physically shared height field remains visible from its upper side.
    material.backFaceCulling = false;
    material.twoSidedLighting = true;
    const magma = Math.max(0, Math.min(1, this.style.magma));
    const ice = Math.max(0, Math.min(1 - magma, this.style.ice * (1 - magma)));
    const water = Math.max(
      0,
      Math.min(1 - magma - ice, this.style.liquidWater * (1 - magma)),
    );
    const solid = Math.max(0, 1 - magma - ice - water);
    material.diffuseColor = new Color3(
      1 * magma + 0.65 * ice + 0.08 * water + 0.45 * solid,
      0.08 * magma + 0.78 * ice + 0.28 * water + 0.42 * solid,
      0.01 * magma + 0.9 * ice + 0.45 * water + 0.35 * solid,
    );
    material.emissiveColor = new Color3(0.8 * magma, 0.06 * magma, 0);
    material.specularColor = this.style.uncertain
      ? new Color3(0.15, 0.05, 0.15)
      : new Color3(0.05 + 0.6 * water, 0.05 + 0.6 * water, 0.05 + 0.6 * water);
    for (const mesh of this.meshes) mesh.material = material;
    this.surfaceMaterial = material;
  }
}
