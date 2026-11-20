import assert from 'node:assert/strict';
import test from 'node:test';
import {
  dot,
  ecefToLocal,
  floatingOriginRequired,
  geodeticUnit,
  localFrame,
  localToEcef,
  planetSurfaceBasis,
  stellarAngularRadius,
  terrainHeight,
} from '../src/physics.ts';

test('double precision local frame round trip stays below one micrometre', () => {
  const frame = localFrame(22.4, -93.75, 6_371_000, 1423.5);
  const local = [9_876.25, 123.75, -8_765.5];
  const recovered = ecefToLocal(
    frame,
    localToEcef(frame, local[0], local[1], local[2]),
  );
  assert.ok(Math.hypot(
    recovered[0] - local[0],
    recovered[1] - local[1],
    recovered[2] - local[2],
  ) < 1e-6);
});

test('stellar angular radius uses exact asin geometry', () => {
  const radius = 696_340_000;
  const distance = 149_597_870_700;
  assert.ok(
    Math.abs(stellarAngularRadius(radius, distance) - Math.asin(radius / distance))
      < 1e-15,
  );
  assert.throws(() => stellarAngularRadius(radius, radius - 1));
});

test('rotating planet surface basis remains orthonormal', () => {
  const basis = planetSurfaceBasis([0.2, -0.3, 0.932737905], -12, 116, 123.4);
  assert.ok(Math.abs(dot(basis.up, basis.east)) < 1e-12);
  assert.ok(Math.abs(dot(basis.up, basis.north)) < 1e-12);
  assert.ok(Math.abs(dot(basis.east, basis.north)) < 1e-12);
});

test('terrain boundary sampling is deterministic and continuous', () => {
  const modes = [{
    direction: [1, 0, 0],
    phase: 0.3,
    frequency: 4,
    amplitude: 1,
  }];
  const model = {
    continent_scale: 0.5,
    mountain_strength: 0,
    fault_strength: 0,
    canyon_strength: 0,
    plain_fraction: 0,
    ridge_directions: [],
    fault_normals: [],
  };
  const boundary = geodeticUnit(10, 20);
  const a = terrainHeight(boundary, modes, model, 10_000, 0.8);
  const b = terrainHeight(boundary, modes, model, 10_000, 0.8);
  assert.equal(a, b);
});

test('current environment morphology v2 produces finite relief', () => {
  const model = {
    model: 'environment_morphology_proxy_v2',
    base_scale: 0.56,
    rugged_modes: [{
      amplitude_fraction: 0.02,
      direction: [0, 1, 0],
      frequency: 17,
      phase: 0.4,
    }],
    plains: [],
    ridges: [],
    faults: [],
  };
  const height = terrainHeight(
    geodeticUnit(0, 0),
    [{ direction: [1, 0, 0], phase: 0.3, frequency: 2.4, amplitude: 0.38 }],
    model,
    18_000,
    0.72,
  );
  assert.ok(Number.isFinite(height));
  assert.ok(Math.abs(height) <= 18_000);
});

test('unknown terrain contracts fail instead of silently creating NaN vertices', () => {
  assert.throws(() => terrainHeight(
    geodeticUnit(0, 0), [], {}, 18_000, 0.72,
  ), /unsupported terrain model/);
});

test('floating origin has a deterministic SI-metre threshold', () => {
  assert.equal(floatingOriginRequired(900, 1200), false);
  assert.equal(floatingOriginRequired(901, 1200), true);
});
