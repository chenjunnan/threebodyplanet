from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from threebody.config.loader import resolve_mapping
from threebody.errors import ThreeBodyError

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
    if not RUN_ID.fullmatch(run_id):
        raise ValueError("invalid run_id")
    candidate = root / run_id
    if candidate.is_symlink() or not candidate.is_dir():
        raise FileNotFoundError(run_id)
    if candidate.resolve().parent != root:
        raise ValueError("run path escapes artifact root")
    return candidate


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "ThreeBodyReadOnly/0.1"

    @property
    def artifact_root(self) -> Path:
        return self.server.artifact_root  # type: ignore[attr-defined,no-any-return]

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
        path = urlsplit(self.path).path
        try:
            if path == "/api/v1/health":
                self._json(HTTPStatus.OK, {"status": "ok", "mode": "read_only"})
                return
            if path == "/api/v1/runs":
                self._json(HTTPStatus.OK, {"runs": list_runs(self.artifact_root)})
                return
            if path == "/api/v1/openapi.json":
                self._static("openapi.json", "application/json; charset=utf-8")
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
            if path == "/app.js":
                self._static("app.js", "text/javascript; charset=utf-8")
                return
            if path == "/styles.css":
                self._static("styles.css", "text/css; charset=utf-8")
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except FileNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "run_not_found"})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "invalid_artifact", "message": str(exc)},
            )

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/api/v1/experiments/validate":
            self._json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": "read_only", "message": "task controls are not available"},
            )
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                raise ValueError("request body size is invalid")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Content-Type must be application/json")
            raw = json.loads(
                self.rfile.read(content_length), parse_constant=_reject_nonfinite
            )
            if not isinstance(raw, dict) or not isinstance(raw.get("config"), dict):
                raise ValueError("request must contain a config object")
            resolved, digest = resolve_mapping(raw["config"])
            self._json(
                HTTPStatus.OK,
                {
                    "valid": True,
                    "sha256": digest,
                    "resolved": resolved.model_dump(mode="json"),
                },
            )
        except (ThreeBodyError, ValueError, json.JSONDecodeError) as exc:
            self._json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"valid": False, "errors": [{"message": str(exc)}]},
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
        raise ValueError("read-only prototype may bind only to a loopback address")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    server = ThreadingHTTPServer((host, port), ConsoleHandler)
    server.artifact_root = root  # type: ignore[attr-defined]
    return server
