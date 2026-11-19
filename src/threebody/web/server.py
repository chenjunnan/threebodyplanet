from __future__ import annotations

import json
import math
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import ValidationError

from threebody.config.loader import resolve_mapping
from threebody.errors import ConfigError
from threebody.physics.longrun.models import LongRunSettings
from threebody.web.comparison import compare_runs
from threebody.web.jobs import LocalRunController
from threebody.web.outcome_science import read_long_outcome
from threebody.web.planet_scenarios import PlanetScenario, build_scenario
from threebody.web.science import query_body_metadata, query_events, query_trajectory
from threebody.web.thermal_science import read_long_environment
from threebody.web.worldsets import (
    create_worldset,
    list_worldsets,
    load_candidate,
    simulation_settings,
)
from threebody.world.models import WorldInitialState

MAX_REQUEST_BYTES = 1_048_576
RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
MANIFEST_NAMES = (
    "long_run_manifest.json",
    "simulation_manifest.json",
    "thermal_manifest.json",
    "world_outcome_manifest.json",
)


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON value is forbidden: {value}")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_nonfinite)


def _manifest_summary(directory: Path) -> dict[str, Any] | None:
    manifests = []
    for name in MANIFEST_NAMES:
        path = directory / name
        if not path.is_file():
            continue
        value = _load_json(path)
        if not isinstance(value, dict):
            raise ValueError(f"{name} must contain an object")
        manifests.append((name, value))
    if not manifests:
        return None
    primary_name, primary = manifests[0]
    task = primary.get("task") if isinstance(primary.get("task"), dict) else {}
    return {
        "run_id": directory.name,
        "manifest": primary_name,
        "schema_version": primary.get("schema_version"),
        "world_id": primary.get("world_id"),
        "status": task.get("status", primary.get("status", "UNKNOWN")),
        "simulation_time_s": task.get(
            "simulation_time_s", primary.get("t_final_s")
        ),
        "available_manifests": [name for name, _ in manifests],
        "read_only": True,
    }


def list_runs(root: Path) -> list[dict[str, Any]]:
    results = []
    for path in sorted(root.iterdir()):
        if path.is_symlink() or not path.is_dir() or not RUN_ID.fullmatch(path.name):
            continue
        summary = _manifest_summary(path)
        if summary is not None:
            results.append(summary)
    return results


def _run_directory(root: Path, run_id: str) -> Path:
    if run_id in {".", ".."} or not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run_id")
    candidate = root / run_id
    if candidate.is_symlink() or not candidate.is_dir():
        raise FileNotFoundError(run_id)
    if candidate.resolve().parent != root:
        raise ValueError("run path escapes artifact root")
    return candidate


class ScienceConsoleServer(ThreadingHTTPServer):
    def server_close(self) -> None:
        jobs = getattr(self, "jobs", None)
        if jobs is not None:
            jobs.close()
        super().server_close()


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "ThreeBodyConsole/0.8.0"

    @property
    def artifact_root(self) -> Path:
        return self.server.artifact_root  # type: ignore[attr-defined,no-any-return]

    @property
    def jobs(self) -> LocalRunController:
        return self.server.jobs  # type: ignore[attr-defined,no-any-return]

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= MAX_REQUEST_BYTES:
            raise ValueError('request body size is invalid')
        if self.headers.get_content_type() != 'application/json':
            raise ValueError('Content-Type must be application/json')
        origin = self.headers.get('Origin')
        if origin is not None:
            expected = f'http://{self.headers.get("Host", "")}'
            if origin != expected:
                raise ValueError('cross-origin task mutation is forbidden')
        raw = json.loads(self.rfile.read(length), parse_constant=_reject_nonfinite)
        if not isinstance(raw, dict):
            raise ValueError('request must be a JSON object')
        return raw

    def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()

    def _json(self, status: HTTPStatus, value: Any) -> None:
        body = json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False
        ).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _static(self, name: str, content_type: str) -> None:
        body = files("threebody.web.static").joinpath(name).read_bytes()
        self._headers(HTTPStatus.OK, content_type, len(body))
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        path = parts.path
        try:
            if path == "/api/v1/health":
                self._json(HTTPStatus.OK, {"status": "ok", "mode": "local_task_control"})
                return
            if path == "/api/v1/worlds/preset":
                self._static("world_preset.json", "application/json; charset=utf-8")
                return
            if path == "/api/v1/worldsets":
                self._json(HTTPStatus.OK, {"worldsets": list_worldsets(self.artifact_root)})
                return
            if path in {"/api/v1/experiments/compare", "/api/v1/experiments/report"}:
                params = parse_qs(
                    parts.query, keep_blank_values=True, strict_parsing=True,
                )
                if set(params) != {"runs"} or len(params["runs"]) != 1:
                    raise ValueError("required query: runs=id1,id2 (up to eight)")
                ids = params["runs"][0].split(",")
                result = compare_runs(self.artifact_root, ids)
                if path.endswith("/report"):
                    result = {
                        **result,
                        "report_type": "M5B_VERIFIED_SCIENCE_EXPORT",
                        "read_only": True,
                    }
                self._json(HTTPStatus.OK, result)
                return
            if path == "/api/v1/runs":
                self._json(HTTPStatus.OK, {"runs": list_runs(self.artifact_root)})
                return
            job_match = re.fullmatch(r"/api/v1/jobs/([^/]+)", path)
            if job_match:
                self._json(HTTPStatus.OK, self.jobs.status(unquote(job_match.group(1))))
                return
            if path == "/api/v1/openapi.json":
                self._static("openapi.json", "application/json; charset=utf-8")
                return
            outcome_match = re.fullmatch(r"/api/v1/runs/([^/]+)/outcome", path)
            if outcome_match:
                run_id = unquote(outcome_match.group(1))
                directory = _run_directory(self.artifact_root, run_id)
                outcome_root = self.artifact_root / "outcomes"
                if outcome_root.is_symlink() or not outcome_root.is_dir():
                    raise FileNotFoundError(run_id)
                outcome_dir = _run_directory(outcome_root, run_id)
                env_root = self.artifact_root / "environments"
                environment = None
                if env_root.is_dir() and not env_root.is_symlink():
                    try:
                        environment = _run_directory(env_root, run_id)
                    except FileNotFoundError:
                        pass
                self._json(HTTPStatus.OK, read_long_outcome(
                    directory, outcome_dir, environment,
                ))
                return
            thermal_match = re.fullmatch(r"/api/v1/runs/([^/]+)/environment", path)
            if thermal_match:
                run_id = unquote(thermal_match.group(1))
                directory = _run_directory(self.artifact_root, run_id)
                derived_root = self.artifact_root / "environments"
                if derived_root.is_symlink() or not derived_root.is_dir():
                    raise FileNotFoundError(run_id)
                derived = _run_directory(derived_root, run_id)
                params = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
                if set(params) - {"start_s", "end_s", "max_points"}:
                    raise ValueError("unknown environment query parameter")
                if any(len(values) != 1 for values in params.values()):
                    raise ValueError("duplicate environment query parameter")
                def env_time(key: str) -> float | None:
                    if key not in params:
                        return None
                    value = float(params[key][0])
                    if not math.isfinite(value):
                        raise ValueError("non-finite environment time")
                    return value
                self._json(HTTPStatus.OK, read_long_environment(
                    directory, derived,
                    start_s=env_time("start_s"),
                    end_s=env_time("end_s"),
                    max_points=int(params.get("max_points", ["500"])[0]),
                ))
                return
            bodies_match = re.fullmatch(r"/api/v1/runs/([^/]+)/bodies", path)
            if bodies_match:
                if parts.query:
                    raise ValueError("body metadata does not accept query parameters")
                run_id = unquote(bodies_match.group(1))
                directory = _run_directory(self.artifact_root, run_id)
                if not (directory / "long_run_manifest.json").is_file():
                    raise ValueError("body metadata requires M5A archive")
                self._json(HTTPStatus.OK, query_body_metadata(directory))
                return
            data_match = re.fullmatch(r"/api/v1/runs/([^/]+)/(trajectory|events)", path)
            if data_match:
                run_id = unquote(data_match.group(1))
                directory = _run_directory(self.artifact_root, run_id)
                if not (directory / "long_run_manifest.json").is_file():
                    raise ValueError("range science API requires M5A segmented archive")
                params = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
                allowed = {"start_s", "end_s"}
                if data_match.group(2) == "trajectory":
                    allowed.add("max_points")
                if set(params) - allowed:
                    raise ValueError("unknown range query parameter")
                if any(len(values) != 1 for values in params.values()):
                    raise ValueError("duplicated range query parameter")
                def time_arg(key: str) -> float | None:
                    if key not in params:
                        return None
                    number = float(params[key][0])
                    if not math.isfinite(number):
                        raise ValueError("time must be finite")
                    return number
                start = time_arg("start_s")
                end = time_arg("end_s")
                if data_match.group(2) == "trajectory":
                    count = int(params.get("max_points", ["500"])[0])
                    data = query_trajectory(
                        directory, start_s=start, end_s=end, max_points=count
                    )
                else:
                    data = query_events(directory, start_s=start, end_s=end)
                self._json(HTTPStatus.OK, data)
                return
            match = re.fullmatch(r"/api/v1/runs/([^/]+)/manifest", path)
            if match:
                run_id = unquote(match.group(1))
                directory = _run_directory(self.artifact_root, run_id)
                manifests = {
                    name: _load_json(directory / name)
                    for name in MANIFEST_NAMES
                    if (directory / name).is_file()
                }
                if not manifests:
                    raise FileNotFoundError(run_id)
                self._json(
                    HTTPStatus.OK,
                    {"run_id": run_id, "read_only": True, "manifests": manifests},
                )
                return
            if path in {"/", "/index.html"}:
                self._static("index.html", "text/html; charset=utf-8")
                return
            if path in {
                "/web-core.js", "/navigation.js", "/planet-explorer.js", "/ground-view.js",
                "/planet-globe.js",
            }:
                self._static(path[1:], "text/javascript; charset=utf-8")
                return
            if path == "/app.js":
                self._static("app.js", "text/javascript; charset=utf-8")
                return
            if path == "/science-adapter.js":
                self._static("science-adapter.js", "text/javascript; charset=utf-8")
                return
            if path == "/surface-materials.js":
                self._static("surface-materials.js", "text/javascript; charset=utf-8")
                return
            if path == "/webgl-observer.js":
                self._static("webgl-observer.js", "text/javascript; charset=utf-8")
                return
            if path == "/styles.css":
                self._static("styles.css", "text/css; charset=utf-8")
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except (FileNotFoundError, KeyError):
            self._json(HTTPStatus.NOT_FOUND, {"error": "run_not_found"})
        except (ConfigError, OSError, ValueError, json.JSONDecodeError) as exc:
            self._json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "invalid_artifact", "message": str(exc)},
            )

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        control = re.fullmatch(r"/api/v1/runs/([^/]+)/(pause|resume|cancel|analyze)", path)
        if (
            path not in {
                "/api/v1/experiments/validate", "/api/v1/runs",
                "/api/v1/worldsets", "/api/v1/worlds/run", "/api/v1/planet-scenarios",
            }
            and not control
        ):
            self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "unsupported_operation"})
            return
        try:
            raw = self._body()
            if path == "/api/v1/planet-scenarios":
                if set(raw) != {"run_id", "parameters"}:
                    raise ValueError("required fields: run_id, parameters")
                directory = _run_directory(self.artifact_root, raw["run_id"])
                parameters = PlanetScenario.model_validate_json(
                    json.dumps(raw["parameters"]), strict=True
                )
                self._json(HTTPStatus.OK, build_scenario(directory, parameters))
                return
            if path == "/api/v1/experiments/validate":
                if not isinstance(raw.get("config"), dict):
                    raise ValueError("request must contain a config object")
                resolved, digest = resolve_mapping(raw["config"])
                self._json(HTTPStatus.OK, {
                    "valid": True, "sha256": digest,
                    "resolved": resolved.model_dump(mode="json"),
                })
                return
            if path == "/api/v1/worldsets":
                if set(raw) != {"config", "count"}:
                    raise ValueError("required fields: config, count")
                result = create_worldset(
                    self.artifact_root, raw["config"], raw["count"]
                )
                self._json(HTTPStatus.CREATED, result)
                return
            if path == "/api/v1/worlds/run":
                if set(raw) != {
                    "batch_id", "candidate_index", "run_id",
                    "duration_s", "output_interval_s",
                }:
                    raise ValueError(
                        "required fields: batch_id, candidate_index, run_id, "
                        "duration_s, output_interval_s"
                    )
                run_id = raw["run_id"]
                if (
                    not isinstance(run_id, str)
                    or run_id in {".", ".."}
                    or not RUN_ID.fullmatch(run_id)
                ):
                    raise ValueError("invalid run_id")
                initial = load_candidate(
                    self.artifact_root, raw["batch_id"], raw["candidate_index"]
                )
                settings = simulation_settings(
                    raw["duration_s"], raw["output_interval_s"]
                )
                self._json(HTTPStatus.ACCEPTED, self.jobs.start(
                    run_id, initial, settings,
                ))
                return
            if path == "/api/v1/runs":
                if set(raw) != {"run_id", "initial_state", "settings"}:
                    raise ValueError("required fields: run_id, initial_state, settings")
                run_id = raw["run_id"]
                if (
                    not isinstance(run_id, str)
                    or run_id in {".", ".."}
                    or not RUN_ID.fullmatch(run_id)
                ):
                    raise ValueError("invalid run_id")
                initial = WorldInitialState.model_validate_json(
                    json.dumps(raw["initial_state"], allow_nan=False), strict=True
                )
                settings = LongRunSettings.model_validate_json(
                    json.dumps(raw["settings"], allow_nan=False), strict=True
                )
                self._json(HTTPStatus.ACCEPTED, self.jobs.start(run_id, initial, settings))
                return
            assert control is not None
            run_id, action = unquote(control.group(1)), control.group(2)
            if run_id in {".", ".."} or not RUN_ID.fullmatch(run_id):
                raise ValueError("invalid run_id")
            if raw:
                raise ValueError("control request must contain an empty JSON object")
            if action == "resume":
                result = self.jobs.resume(run_id)
            elif action == "analyze":
                result = self.jobs.analyze(run_id)
            else:
                result = self.jobs.request(run_id, action)
            self._json(HTTPStatus.ACCEPTED, result)
        except (FileNotFoundError, KeyError):
            self._json(HTTPStatus.NOT_FOUND, {"error": "run_not_found"})
        except (ConfigError, ValidationError, ValueError, TypeError) as exc:
            if path == "/api/v1/experiments/validate":
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"valid": False, "errors": [{"message": str(exc)}]},
                )
            else:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "invalid_request", "message": str(exc)},
                )

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_console_server(
    artifact_root: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    root = Path(artifact_root).resolve()
    if not root.is_dir():
        raise ValueError(f"artifact root is not a directory: {root}")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("local task control may bind only to a loopback address")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    server = ScienceConsoleServer((host, port), ConsoleHandler)
    server.artifact_root = root  # type: ignore[attr-defined]
    server.jobs = LocalRunController(root)  # type: ignore[attr-defined]
    return server
