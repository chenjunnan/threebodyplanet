export type MaterialId = (
  | 'basaltic_silicate'
  | 'granitic_silicate'
  | 'iron_nickel'
  | 'sulfur_compounds'
  | 'water_ice'
);

export interface MaterialProfile {
  semantics?: string;
  components: { material_id: MaterialId; mass_fraction: number }[];
}

export interface SurfaceStyle {
  liquidWater: number;
  ice: number;
  magma: number;
  temperatureK: number | null;
  temperatureQuality: string;
  uncertain: boolean;
}

export interface SurfacePalette {
  diffuse: [number, number, number];
  emissive: [number, number, number];
  label: string;
}

const MINERAL_COLORS: Record<MaterialId, [number, number, number]> = {
  basaltic_silicate: [0.24, 0.22, 0.2],
  granitic_silicate: [0.5, 0.43, 0.35],
  iron_nickel: [0.38, 0.2, 0.11],
  sulfur_compounds: [0.5, 0.4, 0.13],
  water_ice: [0.68, 0.75, 0.8],
};
const MATERIAL_LABELS: Record<MaterialId, string> = {
  basaltic_silicate: '玄武质硅酸盐',
  granitic_silicate: '花岗质硅酸盐',
  iron_nickel: '铁镍质',
  sulfur_compounds: '硫化物质',
  water_ice: '水冰质',
};

const clamp01 = (value: number): number => Math.max(0, Math.min(1, value));
const mix = (
  a: [number, number, number],
  b: [number, number, number],
  fraction: number,
): [number, number, number] => {
  const q = clamp01(fraction);
  return [
    a[0] + (b[0] - a[0]) * q,
    a[1] + (b[1] - a[1]) * q,
    a[2] + (b[2] - a[2]) * q,
  ];
};

function compositionColor(profile?: MaterialProfile): {
  color: [number, number, number];
  dominant: MaterialId;
} {
  const components = profile?.components.filter(
    (item) => Number.isFinite(item.mass_fraction) && item.mass_fraction >= 0,
  ) ?? [];
  const total = components.reduce((sum, item) => sum + item.mass_fraction, 0);
  if (!(total > 0)) {
    return { color: MINERAL_COLORS.basaltic_silicate, dominant: 'basaltic_silicate' };
  }
  const color: [number, number, number] = [0, 0, 0];
  let dominant = components[0]!.material_id;
  for (const item of components) {
    const fraction = item.mass_fraction / total;
    const mineral = MINERAL_COLORS[item.material_id];
    color[0] += mineral[0] * fraction;
    color[1] += mineral[1] * fraction;
    color[2] += mineral[2] * fraction;
    const current = components.find((candidate) => candidate.material_id === dominant)!;
    if (item.mass_fraction > current.mass_fraction) dominant = item.material_id;
  }
  return { color, dominant };
}

export function surfacePalette(
  style: SurfaceStyle,
  profile?: MaterialProfile,
): SurfacePalette {
  const composition = compositionColor(profile);
  const temperature = style.temperatureK;
  let color = composition.color;
  let regime = '温度不可用';
  if (temperature !== null && Number.isFinite(temperature)) {
    if (temperature < 180) {
      color = mix(color, [0.58, 0.63, 0.7], 0.35);
      regime = '极寒岩面';
    } else if (temperature < 273.15) {
      color = mix(color, [0.46, 0.5, 0.54], 0.22);
      regime = '冰点下岩面';
    } else if (temperature < 330) {
      regime = '温和岩面';
    } else if (temperature < 600) {
      color = mix(color, [0.3, 0.17, 0.09], (temperature - 330) / 540);
      regime = '干热岩面';
    } else if (temperature < 1000) {
      color = mix(color, [0.15, 0.055, 0.025], 0.45 + (temperature - 600) / 1000);
      regime = '高温灼烧岩面';
    } else {
      color = mix(color, [0.11, 0.025, 0.01], 0.85);
      regime = '近熔融高温岩面';
    }
  }
  // Water inventory controls separate basin geometry. It may darken exposed
  // rock as a wetness proxy but must not paint the whole planet blue.
  color = mix(color, [0.08, 0.1, 0.11], Math.min(0.18, style.liquidWater * 0.18));
  color = mix(color, [0.78, 0.84, 0.9], style.ice * 0.8);
  color = mix(color, [0.92, 0.11, 0.015], style.magma * 0.82);
  return {
    diffuse: color.map(clamp01) as [number, number, number],
    emissive: [0.9 * style.magma, 0.075 * style.magma, 0.005 * style.magma],
    label: regime + ' · ' + MATERIAL_LABELS[composition.dominant],
  };
}
