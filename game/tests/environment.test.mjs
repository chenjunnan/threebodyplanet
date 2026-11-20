import assert from 'node:assert/strict';
import test from 'node:test';
import {
  environmentField,
  surfaceStyle,
  weatherState,
} from '../src/environment.ts';

const environment = {
  fields: {
    liquid_water: {value: 18, unit: 'kg/m2', quality: 'COMPUTED'},
    ice: {value: 2, unit: 'kg/m2', quality: 'COMPUTED'},
    magma_fraction: {value: 0.25, unit: '1', quality: 'PARAMETERIZED'},
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
    uncertain: false,
  });
  assert.deepEqual(weatherState(environment), {
    state: 'RAIN',
    phase: 'RAIN',
    events: ['STRONG_WIND'],
    quality: 'PARAMETERIZED',
  });
});
