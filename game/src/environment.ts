import type { EnvironmentPoint } from './api';
import type { SurfaceStyle } from './terrain-manager';

export interface FieldValue {
  value: number | null;
  unit: string;
  quality: string;
}

export function environmentField(
  environment: EnvironmentPoint,
  name: string,
): FieldValue {
  const fields = environment.fields as Record<string, FieldValue> | undefined;
  const value = fields?.[name];
  if (!value || (value.value !== null && !Number.isFinite(value.value))) {
    return { value: null, unit: '', quality: 'UNAVAILABLE' };
  }
  return value;
}

export function weatherState(environment: EnvironmentPoint): {
  state: string;
  phase: string;
  events: string[];
  quality: string;
} {
  const weather = environment.weather as {
    state?: string;
    precipitation_phase?: string;
    active_events?: string[];
    quality?: string;
  } | undefined;
  return {
    state: weather?.state ?? 'UNAVAILABLE',
    phase: weather?.precipitation_phase ?? 'NONE',
    events: weather?.active_events ?? [],
    quality: weather?.quality ?? 'UNAVAILABLE',
  };
}

export function surfaceStyle(environment: EnvironmentPoint): SurfaceStyle {
  const liquid = environmentField(environment, 'liquid_water');
  const ice = environmentField(environment, 'ice');
  const magma = environmentField(environment, 'magma_fraction');
  const totalWater = Math.max(1, (liquid.value ?? 0) + (ice.value ?? 0));
  return {
    liquidWater: Math.max(0, Math.min(1, (liquid.value ?? 0) / totalWater)),
    ice: Math.max(0, Math.min(1, (ice.value ?? 0) / totalWater)),
    magma: Math.max(0, Math.min(1, magma.value ?? 0)),
    uncertain: [liquid, ice, magma].some(
      (entry) => entry.quality === 'UNCERTAIN' || entry.quality === 'UNAVAILABLE',
    ),
  };
}

export function formatField(field: FieldValue, digits = 1): string {
  if (field.value === null) return '不可用 [' + field.quality + ']';
  return field.value.toFixed(digits) + ' ' + field.unit + ' [' + field.quality + ']';
}
