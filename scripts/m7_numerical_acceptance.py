"""Real IAS15 + M3 cadence comparison; no synthetic trajectories or climate claims."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from scripts.m6_five_year_acceptance import AU_M, initial_world
from threebody.common.json import sha256_json
from threebody.physics.longrun.models import LongRunSettings
from threebody.physics.longrun.reader import SegmentedTrajectoryReader
from threebody.physics.longrun.runner import start_long_run
from threebody.web.pipeline import derive_run_science
from threebody.web.thermal_science import read_long_environment
from threebody.web.worldsets import simulation_settings


def run(root: Path, days: int = 30) -> dict:
    root.mkdir(parents=True, exist_ok=False)
    results = []
    for compact in (False, True):
        initial = initial_world()
        if compact:
            bodies = []
            for body, x in zip(initial.bodies, (-2., 2., 4., -1.), strict=True):
                bodies.append(body.model_copy(update={'position_m': (x*AU_M, 0., 0.)}))
            # Restore the barycentric inertial origin; leave all forces unmodified.
            mass = sum(b.mass_kg for b in bodies)
            com = sum(b.mass_kg*b.position_m[0] for b in bodies)/mass
            bodies = [b.model_copy(update={'position_m':
                      (b.position_m[0]-com, 0., 0.)}) for b in bodies]
            initial = initial.model_copy(update={
                'bodies': tuple(bodies),
                'generator_id': 'm7_compact_reference', 'generator_version': '0.1.0',
                'config_sha256': sha256_json({'fixture': 'm7-compact-v1'}),
            })
        case = 'compact-triple' if compact else 'wide-controlled'
        outputs = []
        for interval, epsilon in ((21600., 1e-9), (10800., 1e-9), (10800., 1e-12)):
            run_id = f'{case}-{int(interval)}-eps{epsilon:.0e}'
            settings = simulation_settings(days*86400., interval)
            raw = settings.model_dump(mode='json')
            raw['resources']['max_wall_seconds_per_invocation'] = None
            raw['epsilon'] = epsilon
            settings = LongRunSettings.model_validate(raw, strict=True)
            integration = start_long_run(initial, settings, root/run_id, run_id=run_id)
            assert integration.manifest.task.status == 'COMPLETED'
            samples = list(SegmentedTrajectoryReader(root/run_id).iter_samples())
            first = samples[0].diagnostics
            energy_scale = first.kinetic_energy_j+abs(first.potential_energy_j)
            energy_error = max(abs(s.diagnostics.total_energy_j-first.total_energy_j)
                               for s in samples)/energy_scale
            assert energy_error < 1e-9
            science = derive_run_science(root, run_id)
            assert science['thermal'] == science['outcome'] == 'COMPLETED'
            env = read_long_environment(root/run_id, root/'environments'/run_id, max_points=2)
            assert all(v is not None and v < 1e-4
                       for v in env['thermal_energy_residual'].values())
            outputs.append({'interval_s': interval, 'ias15_epsilon': epsilon,
                            'sample_count': len(samples),
                            'energy_scaled_error': energy_error,
                            'thermal_energy_residual': env['thermal_energy_residual'],
                            'final_c0_k': env['rows'][-1]['c0_temperature_k'],
                            'final_c1_k': env['rows'][-1]['c1_temperature_k'],
                            'final_positions_m': [list(b.position_m) for b in samples[-1].bodies]})
        c0 = abs(outputs[0]['final_c0_k']-outputs[1]['final_c0_k'])
        c1 = max(abs(a-b) for a, b in zip(outputs[0]['final_c1_k'],
                                         outputs[1]['final_c1_k'], strict=True))
        positions = float(np.max(np.abs(np.array(outputs[0]['final_positions_m'])-
                                        np.array(outputs[1]['final_positions_m']))))
        assert max(c0, c1) < .5, (case, c0, c1)
        epsilon_difference = float(np.max(np.abs(np.array(outputs[1]['final_positions_m'])-
                                                np.array(outputs[2]['final_positions_m']))))
        assert epsilon_difference/AU_M < 1e-8
        results.append({'case': case, 'duration_days': days, 'runs': outputs,
                        'final_c0_difference_k': c0, 'final_c1_max_difference_k': c1,
                        'final_position_max_difference_m': positions,
                        'epsilon_final_position_max_difference_m': epsilon_difference,
                        'epsilon_comparison_limit_au': 1e-8,
                        'temperature_comparison_limit_k': .5})
    report = {'schema_version': 'm7-numerical-acceptance-v1', 'status': 'PASS',
              'kind': 'REAL_IAS15_AND_M3', 'results': results,
              'limits': ['Two finite-duration configurations, not proof of long-term stability.',
                         'Compact fixture is not a measured Lyapunov/chaos classification.',
                         'Output-cadence comparison tests thermal forcing reconstruction.',
                         'This does not prove atmospheric, geology or weather model fidelity.']}
    (root/'report.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-root', type=Path, required=True)
    parser.add_argument('--days', type=int, default=30, choices=range(1, 31))
    args = parser.parse_args()
    print(json.dumps(run(args.artifact_root, args.days), indent=2, allow_nan=False))
