"""Bounded four-body graphics benchmark; fixtures are not scientific integrations."""
from __future__ import annotations

import argparse
import json
import os
import platform
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

from threebody.web.server import create_console_server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--frames', type=int, default=120)
    args = parser.parse_args()
    if not 20 <= args.frames <= 1000:
        parser.error('--frames must be within 20..1000')
    if args.out.exists():
        parser.error('--out already exists; choose a new report path')
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix='m63-benchmark-') as root:
        server = create_console_server(Path(root), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as api:
                browser = api.chromium.launch(
                    executable_path=os.environ.get('THREEBODY_TEST_CHROMIUM'),
                    headless=True,
                    args=['--enable-unsafe-swiftshader', '--use-angle=swiftshader'],
                )
                page = browser.new_page(viewport={'width': 1300, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/#/orbit',
                          wait_until='networkidle')
                page.evaluate('''() => {
                  const ids=ThreeBodyScienceAdapter.bodyIds;
                  const samples=Array.from({length:2000},(_,i)=>({
                    t_s:i*21600,positions_m:Object.fromEntries(ids.map((id,b)=>[
                      id,[Math.cos(i*.004+b)*1e11*(b+1),
                      Math.sin(i*.004+b)*1e11*(b+1),Math.sin(i*.002+b)*5e10]
                    ]))
                  }));
                  orbitSamples=ThreeBodyScienceAdapter.validateTrajectory({
                    run_id:'graphics-benchmark',world_id:'NOT_A_SCIENTIFIC_SIMULATION',
                    units:{time:'s',position:'m'},
                    display_sampling:'uniform_record_index_no_interpolation',
                    display_count:2000,sample_count:2000,samples
                  },'graphics-benchmark').samples;
                  orbitIndex=1999;orbitEventItems=[];orbitDraw();
                }''')
                profiles = []
                for mode in ('canvas-static', 'canvas-replay', 'webgl-static',
                             'webgl-replay', 'webgl-focus-replay'):
                    gpu = mode.startswith('webgl')
                    if gpu and not page.locator('#enable-webgl').is_checked():
                        page.locator('#enable-webgl').click()
                        if not page.locator('#enable-webgl').is_checked():
                            raise RuntimeError('WebGL benchmark requires a working WebGL2 context')
                    canvas = page.locator('#orbit-webgl' if gpu else '#orbit-canvas')
                    canvas.scroll_into_view_if_needed()
                    profiles.append(page.evaluate('''async ({mode,frames}) => {
                      const gpu=mode.startsWith('webgl');
                      const replay=mode.includes('replay');
                      const original=JSON.stringify(orbitSamples);
                      if(gpu)webglRenderer.focus(mode.includes('focus')?'planet-1':null);
                      const gl=gpu?document.querySelector('#orbit-webgl').getContext('webgl2'):null;
                      const durations=[],intervals=[];
                      let previous=null;
                      for(let frame=-10;frame<frames;frame++){
                        const now=await new Promise(resolve=>requestAnimationFrame(resolve));
                        if(frame>=0 && previous!==null)intervals.push(now-previous);
                        const start=performance.now();
                        orbitIndex=replay?2000-frames+frame:1999;
                        orbitDraw();
                        // Include GPU completion; this is not just JS command submission.
                        if(gl)gl.finish();
                        const elapsed=performance.now()-start;
                        if(frame>=0)durations.push(elapsed);
                        previous=now;
                      }
                      const stats=xs=>{
                        const sorted=xs.slice().sort((a,b)=>a-b);
                        return {median:sorted[Math.floor(sorted.length*.5)],
                          p95:sorted[Math.ceil(sorted.length*.95)-1],max:sorted.at(-1)};
                      };
                      if(original!==JSON.stringify(orbitSamples))throw Error('source mutated');
                      if(gl&&gl.getError()!==gl.NO_ERROR)throw Error('WebGL rendering error');
                      return {mode,frames,draw_timing_semantics:
                        gpu?'DRAW_PLUS_GL_FINISH':'CANVAS_API_SUBMISSION',
                        replay_index_range:replay?[2000-frames,1999]:[1999,1999],
                        draw_wall_ms:stats(durations),
                        raf_interval_ms:stats(intervals),source_unchanged:true,
                        raf_p95_within_60hz_budget:stats(intervals).p95<=1000/60,
                        draw_p95_within_60hz_budget:stats(durations).p95<=1000/60};
                    }''', {'mode': mode, 'frames': args.frames}))
                if errors:
                    raise RuntimeError(f'browser errors: {errors}')
                report = {
                    'schema_version': 'm63-observer-benchmark-v1',
                    'measured_at_utc': datetime.now(UTC).isoformat(),
                    'fixture_kind': 'GRAPHICS_FIXTURE_NOT_PHYSICS',
                    'sample_count': 2000, 'body_count': 4, 'canvas_size': [940, 500],
                    'materials': True, 'thermal_panels': 'NOT_BENCHMARKED',
                    'renderer': 'Chromium headless / forced SwiftShader software WebGL2',
                    'browser_version': browser.version, 'platform': platform.platform(),
                    'warmup_frames_per_profile': 10, 'profiles': profiles,
                    'limits': ('Local graphics measurement, not scientific performance '
                               'or a universal FPS guarantee.'),
                }
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write('\n')
    print(args.out)


if __name__ == '__main__':
    main()
