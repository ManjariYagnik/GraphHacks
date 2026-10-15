# Phase 1 implementation status

Updated: 2026-10-15 (Asia/Kolkata).

## Completed

- Inspected the empty workspace and all ancestor paths for applicable `AGENTS.md`;
  none were present. There was no existing code or Git metadata to preserve.
- Added an installable Python 3.12 package, `ringlens` console command, `uv.lock`,
  pytest development setup, and Ruff lint/format configuration.
- Added Pydantic v2/settings configuration for all five requested variables.
  Environment values override the working directory's `.env`; default graph is
  `ringlens_demo`. Secrets use `SecretStr`, hidden validation input, and sanitized
  CLI/database errors.
- Added a synchronous official-client wrapper for parameterized reads/writes,
  connection/query/socket deadlines, explicit errors, and connection cleanup.
  Reads use `GRAPH.RO_QUERY`; writes use `GRAPH.QUERY`. No runtime substitute store.
- Added a read-only doctor using `PING`, `GRAPH.CONFIG GET TIMEOUT_DEFAULT`, and
  `GRAPH.LIST`. An absent application graph is valid in Phase 1.
- Added a loopback-only, authenticated Compose service, module-aware health check,
  persistent named volume, AOF, RDB snapshot policy, and graceful shutdown.
- Added real-database smoke and restart-persistence tests with UUID graph names,
  ownership checks, and per-graph cleanup. They never flush a database, delete an
  existing application graph, or remove a volume.
- Added setup/shutdown documentation and excluded root/nested `evaluation/` from
  Docker contexts. Runtime package distributions include only package code.

## Decisions and official verification

- Python is constrained to `>=3.12,<3.13` for this phase. uv resolved and installed
  the official FalkorDB client 1.8.0. Redis is a direct dependency because the wrapper
  uses its official client's exception classes and explicitly disables retries.
- Verified the released [v4.20.7](https://github.com/FalkorDB/FalkorDB/releases/tag/v4.20.7)
  and its [Docker Hub tag metadata](https://hub.docker.com/v2/repositories/falkordb/falkordb-server/tags/v4.20.7).
  The registry returned active amd64/arm64 images and manifest digest
  `sha256:538fe1867a0a5dec55462eff5c1ea5edfc36246a9aa571cf1705b8b3f3d0f32c`.
  Compose pins that release and digest, rather than `latest`.
- Checked the [Docker guide](https://docs.falkordb.com/operations/docker),
  [persistence guide](https://docs.falkordb.com/operations/durability/persistence),
  released [Dockerfile.server](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/build/docker/Dockerfile.server),
  and [run.sh](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/build/docker/run.sh).
  The versioned source and persistence guide agree on `/var/lib/falkordb/data`;
  some general Docker examples still use `/data`. We use the confirmed full path.
  The module is `/var/lib/falkordb/bin/falkordb.so`. Compose invokes Redis with this
  module directly to preserve quoted passwords (the supplied script splits
  `REDIS_ARGS` without quoting). The server-only image adds no dashboard.
- Followed the [durability guide](https://docs.falkordb.com/operations/durability):
  AOF `appendonly yes`, `appendfsync everysec`, and RDB `save 60 1`, all under the
  mounted data directory. `noeviction` avoids silently evicting graph keys.
  Approximately one second of writes can be lost on a crash. Orderly restart
  verification is not a power-loss or backup-recovery guarantee.
- Checked the [official Python client](https://github.com/FalkorDB/falkordb-py),
  its [graph API source](https://github.com/FalkorDB/falkordb-py/blob/main/falkordb/graph.py),
  and the installed client signatures/implementation. `ro_query` and `query` accept
  `params` and millisecond `timeout`. Transport connect/read limits are 3/12 seconds;
  query default/max limits are 5,000/10,000 milliseconds. Failed writes are never
  retried automatically because their outcomes may be unknown.

## Commands run and outcomes

The host initially had Python 3.9.6 and no uv, Python 3.12, or Docker on PATH.
uv 0.13.1 and Python 3.12.15 were downloaded into ignored `.tools/`, and the package
was installed into ignored `.venv/`. Shell network access required sandbox approval;
the first pip/uv/curl attempts failed DNS/cache access, and approved retries worked.
No system Python installation was modified.

For reproducibility in this workspace, the uv commands below were run with:

```sh
export UV_CACHE_DIR="$PWD/.tools/uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.tools/python"
export UV_PYTHON_BIN_DIR="$PWD/.tools/bin"
export PATH="$PWD/.tools/uv-package/bin:$PATH"
```

| Command/check | Outcome |
| --- | --- |
| `python3 -m pip install --target .tools/uv-package uv` | Passed with network approval |
| `uv python install 3.12` | Installed 3.12.15 with network approval |
| `uv sync` | Resolved lockfile and installed package/dependencies with network approval |
| `uv lock --check --offline` | Passed |
| `uv run --locked python -c 'import ringlens; print(ringlens.__version__)'` | Passed, `0.1.0` |
| `uv run --locked ringlens --help` | Passed, exposes `doctor` |
| `uv run --locked ringlens doctor` | Expected exit 1: actionable database connection error |
| `RINGLENS_FALKORDB_PORT=invalid .venv/bin/ringlens doctor` | Expected exit 2: readable variable/type error |
| `uv run --locked ruff check .` | Passed after fixes |
| `uv run --locked ruff format --check .` | Passed after formatting |
| `uv run --locked pytest` | 35 passed, 2 database tests explicitly skipped by default |
| `uv run --locked pytest tests/integration --run-integration` | Failed setup: FalkorDB unreachable; persistence test skipped |
| `uv build --offline` | Built wheel and source distribution; inspected archive contents |
| Ruby `YAML.load_file("compose.yaml")` and volume-path assertion | Passed syntax/path check; not a Compose runtime check |
| `docker compose version` | Exit 127: `docker` command not found |

Initial Ruff checks also scanned the ignored local tool downloads because this
directory had no Git repository. Added an explicit `.tools` exclusion and fixed
project formatting/import issues. No downloaded dependency code was changed.

Git was initialized with `git init -b main` to meet the committed-lockfile
requirement. The Phase 1 source, configuration, tests, docs, and `uv.lock` are
included in the initial `Implement RingLens Phase 1 foundation` commit. Local
tools, virtual environments, caches, distributions, and credentials are ignored.
Staged content is checked with `git diff --cached --check` before committing.

## Blockers and checks still required

Docker CLI/Desktop is absent (`command -v docker` found nothing; expected binary
paths and `/Applications/Docker.app` were absent). No running FalkorDB endpoint was
available. Consequently these have **not passed**:

- Docker Compose schema/configuration validation and image startup/health checks.
- Real graph marker write/read, server-enforced read-only behavior, and cleanup.
- Retaining a marker across database restart and deleting its dedicated test graph.
- Doctor success against a real FalkorDB server.

Install/start Docker, configure `.env`, then run:

```sh
uv sync --locked
docker compose config --quiet
docker compose up -d --wait
uv run ringlens doctor
uv run pytest tests/integration --run-integration
uv run pytest tests/integration --run-persistence
docker compose down
```

These commands preserve the volume. The persistence test restarts the local
database and briefly interrupts its clients. No database graphs or volumes were
deleted during this implementation.

## Phase 2 handoff

Add the graph schema, synthetic dataset, and ingestion next. Treat FalkorDB as the
authoritative runtime store; discovered relationships must come from graph queries.
The default graph remains absent until the first write. Test-only `Phase1Marker`
nodes do not define an application schema. Use parameterized queries, preserve
explicit error handling, and consider idempotency before retrying ingestion writes.
Keep evaluation labels outside runtime artifacts. Scoring, orchestration, API, and
Streamlit remain deferred. Complete the Docker checks above before relying on local
durability; Phase 1 does not claim these unavailable checks passed.
