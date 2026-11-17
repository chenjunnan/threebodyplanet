# P0-M5B read-only prototype report

Date: 2026-11-17
Branch: `feature/p0-m5b-readonly-console`
Scope: M5B-01 read-only archive browser and M5B-02 backend configuration validation foundation

## Implemented

- Added a dependency-free local HTTP console bound to loopback only. It serves packaged responsive HTML,
  CSS and JavaScript plus versioned `/api/v1` endpoints.
- `GET /api/v1/runs` discovers only direct, non-symlink artifact directories and reports archived status,
  world ID and server-authoritative physical `simulation_time_s` without recomputing science.
- `GET /api/v1/runs/{run_id}/manifest` returns committed M2/M3/M4/M5 manifest JSON. Run IDs are strictly
  allowlisted and resolved paths cannot escape the configured artifact root.
- `POST /api/v1/experiments/validate` accepts JSON mappings up to 1 MiB and uses the same preset merge,
  strict Pydantic models, rule validation and SHA-256 implementation as the CLI. Unknown fields and
  non-finite JSON are rejected; no YAML, shell expression, `eval` or filesystem path is executed.
- Packaged OpenAPI 3.1 metadata documents the deliberately small API surface.
- The UI explicitly says it is read-only, displays SI seconds from manifests, and contains no pause,
  resume, cancel or start controls. Any unimplemented POST returns `405 read_only`.
- Responses set CSP, `nosniff`, no-referrer and no-store headers. Non-loopback binding is rejected.

Run locally:

```bash
threebody web serve --artifact-root runs --host 127.0.0.1 --port 8765
```

## Verification

Four HTTP/API tests cover manifest discovery and authoritative time, valid/invalid backend configuration,
absence of fake task controls, response security headers, path traversal and symlink exclusion. The
workspace sandbox forbids socket creation, so the loopback HTTP tests were run in the approved unsandboxed
test process; all four passed.

```text
pytest -q:                       162 passed
ruff check .:                    passed
python -m compileall -q src tests: passed
```

## Known limits

- This is local/single-user and read-only. It has no authentication, database, worker, task mutation,
  asynchronous pause/resume, long-trajectory range API, VIS2 embedding or report export.
- Validation errors currently include Pydantic field locations in their messages but are not yet normalized
  into a dedicated per-field API schema.
- The browser surface was checked through API/static asset tests; automated visual browser QA is not
  available in this workspace.
- M5B-03 controls must wait for a reviewed server-authoritative task service. Adding buttons before that
  service exists would be false functionality and is intentionally prohibited.
