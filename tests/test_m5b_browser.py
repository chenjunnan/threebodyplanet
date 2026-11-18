"""Real Chromium smoke: user-world -> REBOUND -> thermal -> M4 -> 3D -> export.

Run with: pip install playwright && python -m playwright install chromium
          pytest tests/test_m5b_browser.py -v
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright


@contextmanager
def _live_server(artifact_root=None):
    with tempfile.TemporaryDirectory(prefix="threebody-browser-") as temporary:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [
                sys.executable, "-m", "threebody", "web", "serve",
                "--artifact-root", str(artifact_root or temporary), "--host", "127.0.0.1",
                "--port", str(port),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail("Web server exited before browser test")
                try:
                    with urllib.request.urlopen(
                        base + "/api/v1/health", timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    time.sleep(.1)
            else:
                pytest.fail("Web server did not start")
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _navigate(page, route):
    page.evaluate("route => {location.hash='#/'+route;}", route)
    page.locator('[data-page="'+route+'"]').wait_for(state='visible')


@pytest.mark.browser
def test_real_browser_world_3d_drag_science_and_report():
    with _live_server() as url, sync_playwright() as api:
        browser = api.chromium.launch(
            executable_path=os.environ.get("THREEBODY_TEST_CHROMIUM"), headless=True,
        )
        context = browser.new_context(accept_downloads=True, viewport={
            "width": 1300, "height": 1000,
        })
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="domcontentloaded")
        assert not page.locator(".task-advanced").evaluate(
            "(element) => element.open"
        )
        assert page.locator("#physical-progress").count() == 1
        assert page.locator("#analysis-phase").count() == 1
        page.locator("#world-advanced-config").wait_for(state="attached")
        page.wait_for_function(
            "() => document.querySelector('#world-advanced-config')"
            ".value.includes('stochastic_conics')"
        )
        page.locator("#world-seed").fill("20261116")
        page.locator("#world-count").fill("4")
        page.locator("#world-generate").click()
        selectable = page.locator(".world-card:not([disabled])")
        selectable.first.wait_for(timeout=20000)
        selectable.first.click()
        page.locator("#world-duration").select_option("60")
        page.locator("#world-sampling").select_option("10")
        page.locator("#world-run-id").fill("browser-e2e")
        page.locator("#world-start").click()
        page.wait_for_function(
            """() => document.querySelector('#job-state').textContent.includes(
                '科学派生 COMPLETED'
            )""",
            timeout=60000,
        )
        page.wait_for_function(
            "() => Number(document.querySelector('#orbit-time').max) >= 2",
            timeout=20000,
        )
        page.wait_for_function(
            "() => document.querySelector('#environment-status').textContent.includes('已验证')",
            timeout=20000,
        )
        assert page.locator("#analysis-phase").inner_text() == "COMPLETED"
        _navigate(page, "environment")
        energy=page.locator("#m6-energy-diagnostics")
        energy.wait_for()
        assert "净热通量" in energy.inner_text()
        assert "瞬时平衡温度" in energy.inner_text()
        assert "归一化能量残差" in energy.inner_text()
        assert "C1最大纬度温差" in energy.inner_text()
        assert "已保存归档：温度有，M4有" in (
            page.locator("#analysis-progress-text").inner_text()
        )
        assert "未生成M4" not in page.locator("#m4-outcome-summary").inner_text()
        c1_summary = page.locator("#c1-temperature-summary")
        assert "纬度温差 ΔT=" in c1_summary.inner_text()
        c1_canvas = page.locator("#c1-canvas")
        c1_canvas.scroll_into_view_if_needed()
        box = c1_canvas.bounding_box()
        assert box is not None
        hover_x = box["x"] + box["width"] * 0.7
        hover_y = box["y"] + box["height"] * 0.4
        page.mouse.move(hover_x, hover_y)
        c1_hover = page.locator("#c1-temperature-hover").inner_text()
        assert "纬度带中心" in c1_hover
        assert " K / " in c1_hover and " °C" in c1_hover
        assert "无插值" in c1_hover
        assert "真实M3采样" in page.locator("#c1-sphere-status").inner_text()
        assert "K" in page.locator("#c1-sphere-legend").inner_text()
        # C0 readout follows the selected actual physical time, not browser play speed.
        assert page.locator("#c0-current-k").inner_text().endswith(" K")
        assert page.locator("#c0-current-celsius").inner_text().endswith(" °C")
        sample = page.locator("#c0-current-k").inner_text()
        sample_c = page.locator("#c0-current-celsius").inner_text()
        assert abs(
            float(sample.removesuffix(" K")) - 273.15
            - float(sample_c.removesuffix(" °C"))
        ) <= 0.011
        page.locator("#orbit-time").evaluate(
            "(node) => { node.value = '0'; "
            "node.dispatchEvent(new Event('input', {bubbles: true})); }"
        )
        assert "t=0.00000000" in page.locator("#c0-current-time").inner_text()
        page.locator("#orbit-time").evaluate(
            "(node) => { node.value = node.max; "
            "node.dispatchEvent(new Event('input', {bubbles: true})); }"
        )
        assert "t=60.0000000" in page.locator("#c0-current-time").inner_text()

        # Verified M5A initial state supplies properties; picker follows exact
        # physical time without manufacturing evolved masses or radii.
        page.evaluate("() => {window.c1Draws=0;const original=drawC1;"
                      "drawC1=(...args)=>{window.c1Draws++;return original(...args);};}")
        _navigate(page, "orbit")
        body_picker=page.locator("#orbit-body-select")
        body_picker.select_option("planet-1")
        details=page.locator("#orbit-body-detail")
        assert "初态质量" in details.inner_text()
        assert "初态半径" in details.inner_text()
        assert "惯性坐标" in details.inner_text()
        assert "t=60.0000000" in details.inner_text()
        page.locator("#orbit-time").evaluate(
            "(node) => {node.value='0';"
            "node.dispatchEvent(new Event('input',{bubbles:true}));}"
        )
        assert "t=0.00000000" in details.inner_text()
        assert page.evaluate("window.c1Draws") == 0
        page.locator("#orbit-time").evaluate(
            "(node) => {node.value=node.max;"
            "node.dispatchEvent(new Event('input',{bubbles:true}));}"
        )
        page.locator("#orbit-focus-body").click()
        page.locator("#orbit-reset-view").click()
        _navigate(page, "orbit")
        canvas = page.locator("#orbit-canvas")
        canvas.scroll_into_view_if_needed()
        before = canvas.evaluate("(node) => node.toDataURL()")
        rect = canvas.bounding_box()
        assert rect is not None
        x, y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 85, y + 45, steps=10)
        page.mouse.up()
        after = canvas.evaluate("(node) => node.toDataURL()")
        assert before != after, "mouse drag did not affect the 3D projection"
        # Adapter rejects any synthetic or tampered cross-run/foreign-unit data.
        rejected=page.evaluate("""() => {
          const a=window.ThreeBodyScienceAdapter;
          for(const candidate of [
            {run_id:'wrong',world_id:'w',units:{time:'s',position:'m'},
              display_sampling:'uniform_record_index_no_interpolation',
              display_count:0,samples:[]},
            {run_id:'ok',world_id:'w',units:{time:'s',position:'km'},
              display_sampling:'uniform_record_index_no_interpolation',
              display_count:0,samples:[]}
          ]){
            try{a.validateTrajectory(candidate,'ok');return false;}
            catch(error){if(!error.message.includes('M6.3 science'))return false;}
          }
          return true;
        }""")
        assert rejected
        toggle=page.locator("#enable-webgl")
        # A host without WebGL2 intentionally unchecks itself and falls back;
        # Playwright .check() would incorrectly treat that as a test failure.
        toggle.click()
        if toggle.is_checked():
            assert page.locator("#orbit-webgl").is_visible()
            glcanvas=page.locator("#orbit-webgl")
            assert glcanvas.evaluate(
                "(node) => node.getContext('webgl2') !== null"
            )
            glcanvas.scroll_into_view_if_needed()
            glbefore=glcanvas.evaluate("(node) => node.toDataURL()")
            glbox=glcanvas.bounding_box()
            assert glbox is not None
            gx,gy=glbox["x"]+glbox["width"]*.5,glbox["y"]+glbox["height"]*.5
            page.mouse.move(gx,gy)
            page.mouse.down()
            page.mouse.move(gx+120,gy+70,steps=6)
            page.mouse.up()
            glafter=glcanvas.evaluate("(node) => node.toDataURL()")
            assert glbefore != glafter
            toggle.uncheck()
            assert canvas.is_visible()
            assert not glcanvas.is_visible()
        else:
            # Headless hosts may disable WebGL2; deterministic fallback required.
            assert canvas.is_visible()
            assert "Canvas" in page.locator("#webgl-status").inner_text()
        _navigate(page, "reports")
        page.locator("#comparison-run-ids").fill("browser-e2e")
        page.locator("#compare-runs").click()
        page.wait_for_function(
            "() => document.querySelector('#comparison-status').textContent.includes('已验证')",
            timeout=20000,
        )
        with page.expect_download(timeout=20000) as future_download:
            page.locator("#download-report").click()
        download = future_download.value
        assert download.suggested_filename == "threebody-science-report.json"
        with open(download.path(), encoding="utf-8") as source:
            report = json.load(source)
        assert report["runs"][0]["run_id"] == "browser-e2e"
        assert report["runs"][0]["classification"] is not None
        _navigate(page, "surface")
        assert page.locator('[data-page]:visible').count() == 1
        radius=page.evaluate("linkedBodyMetadata.bodies['planet-1'].radius_m")
        page.locator('[data-planet="mantle_thickness_m"]').fill(str(radius*.45))
        page.locator('#planet-build').click()
        page.wait_for_function("() => document.querySelector('#planet-status')"
                               ".textContent.includes('真实源采样')")
        assert '赤道平衡温度' in page.locator('#planet-summary').inner_text()
        assert 'star-1' in page.locator('#planet-sky').inner_text()
        assert '假设氧分压' in page.locator('#planet-summary').inner_text()
        with page.expect_download() as future_scenario:
            page.locator('#planet-export').click()
        with open(future_scenario.value.path(),encoding='utf-8') as handle:
            scenario=json.load(handle)
        assert scenario['data_kind']=='PARAMETERIZED_SCENARIO'
        assert scenario['schema_version']=='planet-exploration-v3'
        assert scenario['source']['t_s']==60
        assert len(scenario['contour_levels_m'])==10
        assert scenario['atmospheric_optics']['model'] in {
            'vacuum','three_band_rayleigh_absorption_proxy_v1'}
        if scenario['pressure_pa']>0:
            assert all(r['wind_speed_range_mps'] is not None for r in scenario['regions'])
        else:
            assert all(r['wind_speed_range_mps'] is None for r in scenario['regions'])
        assert all(r['conditional_hazard_probability'] is None for r in scenario['regions'])
        ground=page.locator('#planet-ground')
        ground.scroll_into_view_if_needed()
        if ground.evaluate("n => n.getContext('webgl2')!==null"):
            before=ground.evaluate('n => n.toDataURL()')
            ground.focus()
            ground.press('ArrowRight')
            assert ground.evaluate('n => n.toDataURL()')!=before
            visible=next((star['id'] for star in scenario['sky']
                          if star['above_geometric_horizon']),None)
            if visible:
                page.locator('#planet-lock-star').select_option(visible)
            assert ground.evaluate("n => n.getContext('webgl2').getError()") == 0
        # Surface playback advances only across exact archived samples and rebuilds the sky.
        page.locator('#planet-ground-previous').click()
        page.wait_for_function("() => document.querySelector('#planet-status')"
                               ".textContent.includes('t=50')",timeout=10000)
        page.locator('#planet-ground-previous').click()
        page.wait_for_function("() => document.querySelector('#planet-status')"
                               ".textContent.includes('t=40')",timeout=10000)
        page.locator('#planet-ground-play').click()
        page.wait_for_function("() => document.querySelector('#planet-status')"
                               ".textContent.includes('t=50')",timeout=10000)
        page.locator('#planet-ground-pause').click()
        page.get_by_text('区域地质事件先验（可选）',exact=True).click()
        page.locator('#planet-hazard-definition').fill('test event in each equal-area region')
        page.locator('#planet-hazard-rates').fill('{"r12-c24":0.01}')
        page.locator('#planet-build').click()
        page.wait_for_function("() => document.querySelector('#planet-status')"
                               ".textContent.includes('真实源采样')")
        terrain_map=page.locator('#planet-map')
        terrain_map.scroll_into_view_if_needed()
        bounds=terrain_map.bounding_box()
        terrain_map.click(position={'x':bounds['width']*24.5/48,
                                    'y':bounds['height']*11.5/24})
        assert 'r12-c24' in page.locator('#planet-region').inner_text()
        assert '0.995%' in page.locator('#planet-region').inner_text()
        # Editing an assumption invalidates every old scenario output.
        page.locator('[data-planet="albedo"]').fill('0.4')
        assert page.locator('#planet-export').is_disabled()
        assert page.locator('#planet-summary').inner_text()==''
        page.evaluate("() => {location.hash='#/constructor';}")
        page.locator('[data-page="worlds"]').wait_for(state='visible')
        assert page.locator('[data-page]:visible').count()==1
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not errors, errors
        context.close()
        browser.close()


@pytest.mark.browser
def test_webgl2_shader_and_3d_rotation_with_local_software_renderer():
    """Exercise the real GPU/WebGL shader path, independent of fallback QA.

    Synthetic coordinates here are *only a graphics fixture*, not science data.
    Real IAS15 source binding is tested by the main browser workflow above.
    """
    with _live_server() as url, sync_playwright() as api:
        browser=api.chromium.launch(
            executable_path=os.environ.get("THREEBODY_TEST_CHROMIUM"), headless=True,
            args=["--enable-unsafe-swiftshader","--use-angle=swiftshader"],
        )
        page=browser.new_page(viewport={"width":1300,"height":900})
        errors=[]
        page.on("pageerror",lambda error:errors.append(str(error)))
        page.goto(url,wait_until="domcontentloaded")
        _navigate(page, "orbit")
        page.locator("#enable-webgl").click()
        assert page.locator("#enable-webgl").is_checked(), (
            "Forced SwiftShader WebGL2 should remain enabled"
        )
        canvas=page.locator("#orbit-webgl")
        assert canvas.is_visible()
        assert canvas.evaluate("(node) => node.getContext('webgl2')!==null")
        page.evaluate("""() => {
          const ids=['star-1','star-2','star-3','planet-1'];
          const points=[
            [[-100,0,0],[100,0,0],[0,100,0],[0,0,100]],
            [[-90,20,5],[90,-20,-5],[15,80,20],[20,10,85]]
          ];
          const samples=points.map((row,index)=>({
            t_s:index*10,positions_m:Object.fromEntries(
              row.map((position,i)=>[ids[i],position])
            )
          }));
          const verified=window.ThreeBodyScienceAdapter.validateTrajectory({
            world_id:'graphics-fixture-not-physics',run_id:'gpu-fixture',
            units:{time:'s',position:'m'},
            display_sampling:'uniform_record_index_no_interpolation',
            display_count:2,samples
          },'gpu-fixture');
          orbitSamples=verified.samples;
          orbitSlider.max=1;
          webglRenderer.load(verified.samples);
          orbitSetIndex(1);
        }""")
        canvas.scroll_into_view_if_needed()
        before=canvas.evaluate("(node) => node.toDataURL()")
        pixels=canvas.evaluate("node => node.toDataURL()")
        page.locator("#surface-materials").uncheck()
        page.evaluate("() => webglRenderer.draw(1)")
        assert canvas.evaluate("node => node.toDataURL()") != pixels
        page.locator("#surface-materials").check()
        page.evaluate("() => webglRenderer.draw(1)")
        assert canvas.evaluate("node => node.getContext('webgl2').getError()") == 0
        canvas.scroll_into_view_if_needed()
        # The GPU screen projection is available for deterministic click picking.
        hit=page.evaluate("() => webglRenderer.screenPosition('planet-1')")
        assert hit is not None
        page.mouse.click(hit[0],hit[1])
        assert page.locator("#orbit-body-select").input_value()=="planet-1"
        page.evaluate("() => webglRenderer.focus('planet-1')")
        focused=canvas.evaluate("(node) => node.toDataURL()")
        assert focused!=before
        page.evaluate("() => webglRenderer.resetView()")
        bounds=canvas.bounding_box()
        assert bounds is not None
        x,y=bounds["x"]+bounds["width"]*.5,bounds["y"]+bounds["height"]*.5
        page.mouse.move(x,y)
        page.mouse.down()
        page.mouse.move(x+140,y+80,steps=8)
        page.mouse.up()
        after=canvas.evaluate("(node) => node.toDataURL()")
        assert before!=after,"3D WebGL perspective shader must respond to drag"
        # Integration regression: preserve D materials while restoring old controls.
        original=page.evaluate("JSON.stringify(orbitSamples)")
        state=page.evaluate("webglRenderer.cameraState()")
        canvas.focus()
        canvas.press("ArrowRight")
        assert page.evaluate("webglRenderer.cameraState().yaw") == pytest.approx(state["yaw"]+.2)
        zoom=page.evaluate("webglRenderer.cameraState().zoom")
        canvas.press("+")
        assert page.evaluate("webglRenderer.cameraState().zoom") < zoom
        state=page.evaluate("webglRenderer.cameraState()")
        for _ in range(2):
            page.locator("#enable-webgl").uncheck()
            assert page.evaluate("orbitAzimuth") == pytest.approx(state["yaw"])
            page.locator("#enable-webgl").check()
            assert page.evaluate("webglRenderer.cameraState()") == state
        canvas.scroll_into_view_if_needed()
        box=canvas.bounding_box()
        x,y=box["x"]+box["width"]*.5,box["y"]+box["height"]*.5
        page.mouse.move(x,y)
        page.mouse.down()
        page.mouse.move(x+50,y,steps=5)
        page.mouse.up()
        assert page.evaluate("webglRenderer.cameraState().yaw") == pytest.approx(state["yaw"]+.3)
        page.evaluate("document.querySelector('#orbit-webgl').getContext('webgl2')"
                      ".getExtension('WEBGL_lose_context').loseContext()")
        page.wait_for_function("() => !document.querySelector('#orbit-canvas').hidden")
        assert "上下文丢失" in page.locator("#webgl-status").inner_text()
        assert page.evaluate("orbitAzimuth") == pytest.approx(state["yaw"]+.3)
        plain=page.locator("#orbit-canvas")
        before_rewind=plain.evaluate('node => node.toDataURL()')
        page.evaluate('() => orbitSetIndex(0)')
        page.evaluate('() => orbitSetIndex(1)')
        assert plain.evaluate('node => node.toDataURL()')==before_rewind
        plain.focus()
        plain.press("Home")
        assert page.evaluate("orbitAzimuth") == pytest.approx(.6)
        page.evaluate("() => {orbitFocusId='planet-1';selectOrbitBody(null);orbitDraw();}")
        plain.scroll_into_view_if_needed()
        box=plain.bounding_box()
        page.mouse.click(box["x"]+box["width"]*.5,box["y"]+box["height"]*.5)
        assert page.locator("#orbit-body-select").input_value()=="planet-1"
        page.evaluate("() => orbitSetIndex(0)")
        plain.focus()
        plain.press("Space")
        page.wait_for_function("() => orbitIndex===1 && orbitTimer===null")
        assert page.evaluate("JSON.stringify(orbitSamples)")==original
        assert page.locator("#surface-materials").is_checked()
        assert not errors,errors
        browser.close()


@pytest.mark.browser
def test_surface_materials_equal_area_temperature_and_missing_data():
    """Graphics fixtures check mapping and missing data, not a scientific simulation."""
    with _live_server() as url, sync_playwright() as api:
        browser = api.chromium.launch(
            executable_path=os.environ.get("THREEBODY_TEST_CHROMIUM"), headless=True,
            args=["--enable-unsafe-swiftshader", "--use-angle=swiftshader"],
        )
        page = browser.new_page(viewport={"width": 1300, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url, wait_until="domcontentloaded")
        assert "无可信数据" in page.locator("#c1-sphere-status").inner_text()
        _navigate(page, "environment")
        result = page.evaluate("""() => {
          const m=window.ThreeBodySurfaces;
          const row={t_s:10,c1_temperature_k:[200,400],
            c1_latitude_rad:[Math.asin(-.5),Math.asin(.5)]};
          const thermal={rows:[row,{...row,t_s:20}],sampling_quality:'TEST_ONLY',
            sampling_warning:'fixture warning'};
          const copy=JSON.stringify(thermal);
          const frame=m.thermalFrame(thermal,15);
          const sphere=m.disk(3,256,frame).getContext('2d');
          const pixel=(x,y)=>Array.from(sphere.getImageData(x,y,1,1).data);
          window.surfaceFixture=thermal;
          renderSurfaceThermal(thermal,15);
          return {
            north:pixel(128,64),south:pixel(128,192),corner:pixel(0,0),
            hot:m.temperatureColor(400,200,400),cold:m.temperatureColor(200,200,400),
            before:m.thermalFrame(thermal,0),after:m.thermalFrame(thermal,21),
            bad:m.thermalFrame({rows:[{...row,c1_temperature_k:[NaN,400]}]},10),
            wrongGrid:m.thermalFrame({rows:[{...row,c1_latitude_rad:[-.2,.2]}]},10),
            missing:m.thermalFrame({rows:[{...row,c1_temperature_k:null}]},10),
            unchanged:copy===JSON.stringify(thermal),
          };
        }""")
        assert result["north"] == [*result["hot"], 255]
        assert result["south"] == [*result["cold"], 255]
        assert result["corner"][3] == 0
        for key in ("before", "after", "bad", "wrongGrid", "missing"):
            assert result[key] is None
        assert result["unchanged"]
        assert "相差 5 s" in page.locator("#c1-sphere-status").inner_text()
        assert "fixture warning" in page.locator("#c1-sphere-status").inner_text()
        canvas = page.locator("#c1-sphere")
        canvas.scroll_into_view_if_needed()
        bounds = canvas.bounding_box()
        page.mouse.move(bounds["x"]+bounds["width"]*.5,
                        bounds["y"]+bounds["height"]*.25)
        assert "400" in page.locator("#c1-sphere-reading").inner_text()
        page.evaluate("() => renderSurfaceThermal(null,15)")
        assert "无可信数据" in page.locator("#c1-sphere-status").inner_text()
        assert page.locator("#c1-sphere-legend").is_hidden()
        assert canvas.evaluate("node => node.getContext('2d')"
                               ".getImageData(128,64,1,1).data[3]") == 0
        _navigate(page, "surface")
        # All local material previews are actual nonuniform sphere images.
        for name in ("surface-star-1", "surface-star-2", "surface-star-3", "surface-rock"):
            assert page.locator("#"+name).evaluate("node => {"
                "const c=node.getContext('2d');"
                "return c.getImageData(80,80,1,1).data[3]===255 && "
                "c.getImageData(0,0,1,1).data[3]===0; }")
        assert not errors, errors
        browser.close()


@pytest.mark.browser
def test_collision_archive_markers_stop_and_event_validation(tmp_path):
    from test_m5a_runner import _collision_world, _settings

    from threebody.physics.longrun.runner import start_long_run

    result = start_long_run(
        _collision_world(), _settings(end_s=20.0), tmp_path / 'collision-test',
        run_id='collision-test',
    )
    assert result.manifest.task.status == 'COLLISION'
    with _live_server(tmp_path) as url, sync_playwright() as api:
        browser = api.chromium.launch(
            executable_path=os.environ.get('THREEBODY_TEST_CHROMIUM'), headless=True,
            args=['--enable-unsafe-swiftshader', '--use-angle=swiftshader'],
        )
        page = browser.new_page(viewport={'width': 1300, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url, wait_until='domcontentloaded')
        _navigate(page, 'orbit')
        page.locator('#observe-run-id').fill('collision-test')
        page.locator('#observe-load').click()
        page.wait_for_function("() => document.querySelector('#orbit-terminal-status')"
                               ".textContent.includes('碰撞终止')")
        assert page.evaluate('orbitSamples.at(-1).t_s') == result.manifest.task.simulation_time_s
        source = page.evaluate('JSON.stringify(orbitSamples)')
        assert page.evaluate('activeEventMarker().ids') == ['star-1', 'star-2']
        page.locator('#event-next').click()
        assert '没有后续' in page.locator('#orbit-event-sync').inner_text()
        page.locator('#event-previous').click()
        assert '没有更早' in page.locator('#orbit-event-sync').inner_text()
        # Select the existing event explicitly, pausing display playback at its true endpoint.
        event_index = page.locator('#orbit-events').input_value()
        page.locator('#orbit-events').select_option(event_index)
        assert '时间偏差 0 s' in page.locator('#orbit-event-sync').inner_text()
        plain = page.locator('#orbit-canvas')
        with_marker = plain.evaluate('node => node.toDataURL()')
        page.locator('#event-markers').uncheck()
        assert plain.evaluate('node => node.toDataURL()') != with_marker
        page.locator('#event-markers').check()
        _navigate(page, 'orbit')
        page.locator('#enable-webgl').click()
        assert page.locator('#enable-webgl').is_checked()
        gpu = page.locator('#orbit-webgl')
        with_marker = gpu.evaluate('node => node.toDataURL()')
        page.locator('#event-markers').uncheck()
        assert gpu.evaluate('node => node.toDataURL()') != with_marker
        page.locator('#event-markers').check()
        page.evaluate('() => orbitSetIndex(0)')
        assert page.evaluate('activeEventMarker().ids') == []
        page.locator('#orbit-play').click()
        page.wait_for_function('() => orbitIndex===orbitSamples.length-1 && orbitTimer===null')
        assert page.evaluate('JSON.stringify(orbitSamples)') == source
        assert page.evaluate(
            "document.querySelector('#orbit-webgl').getContext('webgl2').getError()"
        ) == 0
        checks = page.evaluate('''() => {
          const a=ThreeBodyScienceAdapter;
          const trajectory={run_id:'fixture',world_id:'fixture',time_end_s:10,
            samples:[{t_s:0},{t_s:10}]};
          const event={t_s:5,event_type:'CLOSE_ENCOUNTER',body_ids:['star-1','planet-1']};
          const payload={run_id:'fixture',world_id:'fixture',event_count:1,events:[event]};
          const before=JSON.stringify(payload);
          a.validateEvents(trajectory,payload);
          const rejects=[];
          for(const change of [
            {run_id:'other'}, {event_count:2},
            {events:[{...event,t_s:NaN}]},
            {events:[{...event,body_ids:['fake']}]},
            {events:[{...event,event_type:'COLLISION'}]}
          ]){
            try{a.validateEvents(trajectory,{...payload,...change});rejects.push(false);}
            catch(error){rejects.push(error.message.includes('M6.3 science'));}
          }
          return {rejects,unchanged:before===JSON.stringify(payload),
            nearest:a.eventSample(trajectory.samples,event),
            outside:a.eventSample(trajectory.samples,{...event,t_s:11})};
        }''')
        assert all(checks['rejects'])
        assert checks['unchanged']
        assert checks['nearest'] == {'index': 0, 'delta_s': 5}
        assert checks['outside'] is None
        # Near-encounter graphics fixture uses actual source positions.
        page.evaluate("""() => {
          orbitEventItems=[{t_s:(orbitSamples[0].t_s+orbitSamples.at(-1).t_s)/2,
            event_type:'CLOSE_ENCOUNTER',body_ids:['star-1','planet-1'],
            message:'GRAPHICS FIXTURE ONLY'}];
          orbitSelectedEvent=0;orbitSetIndex(0);
        }""")
        assert page.evaluate('activeEventMarker().collision') is False
        assert page.evaluate('activeEventMarker().ids') == ['star-1', 'planet-1']
        assert '时间偏差' in page.locator('#orbit-event-sync').inner_text()
        page.locator('#observe-run-id').fill('missing-run')
        page.locator('#observe-load').click()
        page.wait_for_function("() => document.querySelector('#observe-status')"
                               ".textContent.includes('无法载入')")
        assert page.locator('#orbit-terminal-status').inner_text() == ''
        assert page.evaluate('activeEventMarker().ids') == []
        page.add_init_script('''
          const originalGetContext=HTMLCanvasElement.prototype.getContext;
          HTMLCanvasElement.prototype.getContext=function(kind,...args){
            return kind==='webgl2'?null:originalGetContext.call(this,kind,...args);
          };
        ''')
        page.reload(wait_until='domcontentloaded')
        _navigate(page, 'orbit')
        page.locator('#enable-webgl').click()
        assert not page.locator('#enable-webgl').is_checked()
        assert page.locator('#orbit-canvas').is_visible()
        assert 'WebGL不可用' in page.locator('#webgl-status').inner_text()
        assert not errors, errors
        browser.close()
