"""Real Chromium smoke: user-world -> REBOUND -> thermal -> M4 -> 3D -> export.

Run with: pip install playwright && python -m playwright install chromium
          pytest tests/test_m5b_browser.py -v
"""
from __future__ import annotations

import json
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
def _live_server():
    with tempfile.TemporaryDirectory(prefix="threebody-browser-") as temporary:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [
                sys.executable, "-m", "threebody", "web", "serve",
                "--artifact-root", temporary, "--host", "127.0.0.1",
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


@pytest.mark.browser
def test_real_browser_world_3d_drag_science_and_report():
    with _live_server() as url, sync_playwright() as api:
        browser = api.chromium.launch(headless=True)
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
        assert "已保存归档：温度有，M4有" in (
            page.locator("#analysis-progress-text").inner_text()
        )
        assert "未生成M4" not in page.locator("#m4-outcome-summary").inner_text()
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
        assert not errors, errors
        context.close()
        browser.close()
