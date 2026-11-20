import assert from 'node:assert/strict';
import test from 'node:test';
import {
  environmentField,
  surfaceStyle,
  weatherState,
} from '../src/environment.ts';
import { surfacePalette } from '../src/surface-material.ts';

const environment = {
  fields: {
    liquid_water: {value: 18, unit: 'kg/m2', quality: 'COMPUTED'},
    ice: {value: 2, unit: 'kg/m2', quality: 'COMPUTED'},
    magma_fraction: {value: 0.25, unit: '1', quality: 'PARAMETERIZED'},
    surface_temperature: {value: 754, unit: 'K', quality: 'COMPUTED'},
    surface_pressure: {value: 101325, unit: 'Pa', quality: 'COMPUTED'},
  },
  weather: {
    state: 'RAIN',
    precipitation_phase: 'RAIN',
    active_events: ['STRONG_WIND'],
    quality: 'PARAMETERIZED',
  },
};

test('environment fields retain SI value, unit, and quality together', () => {
  assert.deepEqual(environmentField(environment, 'surface_pressure'), {
    value: 101325,
    unit: 'Pa',
    quality: 'COMPUTED',
  });
  assert.equal(environmentField(environment, 'missing').quality, 'UNAVAILABLE');
});

test('surface and weather visuals derive only from archived state', () => {
  assert.deepEqual(surfaceStyle(environment), {
    liquidWater: 0.9,
    ice: 0.1,
    magma: 0.25,
    temperatureK: 754,
    temperatureQuality: 'COMPUTED',
    uncertain: false,
  });
  assert.deepEqual(weatherState(environment), {
    state: 'RAIN',
    phase: 'RAIN',
    events: ['STRONG_WIND'],
    quality: 'PARAMETERIZED',
  });
});

test('surface palette follows temperature, composition, ice, and melt state', () => {
  const profile = {
    components: [
      { material_id: 'basaltic_silicate', mass_fraction: 0.7 },
      { material_id: 'iron_nickel', mass_fraction: 0.3 },
    ],
  };
  const hot = surfacePalette({
    liquidWater: 0,
    ice: 0,
    magma: 0,
    temperatureK: 754,
    temperatureQuality: 'COMPUTED',
    uncertain: false,
  }, profile);
  assert.match(hot.label, /高温灼烧岩面/);
  assert.ok(hot.diffuse[0] > hot.diffuse[1]);
  assert.ok(hot.diffuse[1] > hot.diffuse[2]);

  const frozen = surfacePalette({
    liquidWater: 0,
    ice: 1,
    magma: 0,
    temperatureK: 160,
    temperatureQuality: 'COMPUTED',
    uncertain: false,
  }, profile);
  assert.ok(frozen.diffuse[2] > hot.diffuse[2]);

  const molten = surfacePalette({
    liquidWater: 0,
    ice: 0,
    magma: 0.8,
    temperatureK: 1500,
    temperatureQuality: 'PARAMETERIZED',
    uncertain: false,
  }, profile);
  assert.ok(molten.emissive[0] > 0.7);
  assert.ok(molten.diffuse[0] > molten.diffuse[1] * 3);
});
