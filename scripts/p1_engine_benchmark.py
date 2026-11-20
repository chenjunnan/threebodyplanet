"""Measure the identical Babylon.js/Three.js evaluation scene in Chromium."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/development/P1_ENGINE_BENCHMARK.json"),
    )
    args = parser.parse_args()
    process = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(args.port)],
        cwd="game",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{args.port}/game/evaluation.html"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base, timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Vite preview did not start")
        results = {}
        with sync_playwright() as api:
            browser = api.chromium.launch(headless=True)
            for engine in ("babylon", "three"):
                page = browser.new_page(viewport={"width": 1280, "height": 720})
                errors: list[str] = []
                page.on(
                    "pageerror",
                    lambda error, target=errors: target.append(str(error)),
                )
                page.goto(base + "?engine=" + engine, wait_until="domcontentloaded")
                page.wait_for_function(
                    "window.__ENGINE_EVALUATION__ !== undefined",
                    timeout=60_000,
                )
                result = page.evaluate("window.__ENGINE_EVALUATION__")
                result["page_errors"] = errors
                results[engine] = result
                page.close()
            browser.close()
        report = {
            "schema_version": "p1-engine-evaluation-v1",
            "scene": (
                "128x128 procedural terrain, three lights, orbit camera, "
                "collision, shared glTF"
            ),
            "results": results,
            "selected_engine": "Babylon.js",
            "selection_note": (
                "Frame timing is measured; game abstractions and project "
                "preference are also considered."
            ),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(report, indent=2, ensure_ascii=False)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
