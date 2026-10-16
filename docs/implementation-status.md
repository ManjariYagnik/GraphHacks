# RingLens implementation status

## Phase 2 — schema and validation

Updated: 2026-10-16 (Asia/Kolkata).

Phase 2 is complete. The user subsequently ran the Docker and database checks
successfully; the live verification below resolves the initial environment blockers.

Implemented the Phase 2 scope by extending Phase 1. No applicable `AGENTS.md` was
found. The repository was clean at the start; existing configuration, wrapper,
CLI, dependencies, lockfile, Docker setup, and test conventions were reused.

### Files and behavior

- `src/ringlens/models.py`: strict exact-field source models, future case models,
  import metadata, and manifest models. Includes integer/range/INR validation,
  finite scores/contributions, lifecycle/window checks, and validated JSON strings
  for evidence. All records forbid extra fields. Source records cannot supply
  `source_hash` or permanent account status.
- `src/ringlens/validation.py`: separate duplicate/reference/ownership/time/amount/
  cutoff checks, base-batch count validation, canonical JSONL row serialization,
  source hashes, and non-circular manifest content hashes. This is pure validation,
  not a generator, file reader, importer, or substitute runtime store.
- `src/ringlens/schema.py`: fixed ten-label/ten-relationship vocabulary, source
  property projection including importer-managed hashes, twenty ID/time range
  indexes, additive initialization with catalog checks and bounded readiness
  polling, and a parameterized refund context query.
- `src/ringlens/graph_store.py`: added read-only graph existence probing through
  the official client. `cli.py`: reused settings/error handling for the new
  `ringlens schema init` command.
- `docs/graph-schema.md`, `docs/data-contract.md`, and `README.md`: document source
  fields versus graph properties/edges, future case memory, exact file metadata,
  canonical bytes/hash preimages, and importer validation/replay obligations.
- Added model, cross-record, hashing, and schema unit tests; extended CLI/wrapper
  tests and disposable-graph integration tests for repeated initialization,
  preservation of a preexisting marker, empty initialization, and refund traversal.

### Decisions and verified source behavior

- Source timestamps/amounts use strict 64-bit integers. Address labels use the
  explicit invented-placeholder form `Synthetic address <token>`. Case IDs use
  `inv_`, `ev_`, `rev_`; source IDs retain the requested five prefixes.
- Source JSON contains only application fields. Foreign keys become edges, but
  remain part of the immutable row's SHA-256. `source_hash` is importer-managed.
- File paths are exactly the five requested filenames. Files are ordered by kind
  in the manifest and rows by ID within each file. A batch content hash covers
  canonical manifest metadata excluding `batch_id`; its ID is `batch_` plus that
  hash. Record/file hashing includes JSONL LF bytes; the descriptor preimage does
  not. The contracts spell out both byte sequences.
- Reviewed the pinned v4.20.7 [index creation tests](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/tests/flow/test_index_create.py),
  [index catalog source](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/src/procedures/proc_list_indexes.c),
  [constraint helpers](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/tests/flow/constraint_utils.py),
  and [property type definitions](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/src/value.h).
  Also checked the official [indexing](https://docs.falkordb.com/cypher/indexing)
  and [data type](https://docs.falkordb.com/datatypes) documentation. Approved curl
  reads were used where web extraction could not retrieve the versioned source.
- The catalog uses a per-property `types` map and `NODE`/`RELATIONSHIP` entity
  types. Initialization checks range indexes specifically and waits for
  `OPERATIONAL`. It does not assume Neo4j syntax or `IF NOT EXISTS` behavior.
- Native `UNIQUE`/`MANDATORY` constraints exist in the pinned source, but **none are
  installed in Phase 2**. Indexes are not uniqueness enforcement. Single-writer
  importer validation against existing graph state remains mandatory, including
  one dataset per graph, ID/hash conflicts, and edge cardinalities. Live constraint
  enforcement has not been verified and is not claimed.
- Only supported scalar/array properties are used. Nested evidence details are a
  validated JSON string, not a nested dictionary property. Case outcomes do not
  add or establish an account status.

### Initial implementation verification commands and outcomes

The existing local `.venv` and ignored workspace uv toolchain were reused. No new
dependencies or lockfile changes were needed. `uv` commands can use the toolchain
environment shown in the Phase 1 history below; `.venv/bin/...` commands run directly.

| Command | Outcome |
| --- | --- |
| `.venv/bin/pytest` | 156 passed; 4 real-database tests skipped by default |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/ruff format --check .` | Passed after formatting |
| `.venv/bin/python -c 'from ringlens.models import Account, Device, Address, Order, Refund, Investigation, Evidence, Review, Dataset, ImportBatch, Manifest'` | All Phase 2 models imported |
| `.venv/bin/ringlens --help` | Passed; includes schema subcommand |
| `.venv/bin/ringlens schema init --help` | Passed |
| `RINGLENS_FALKORDB_PORT=invalid .venv/bin/ringlens schema init` | Expected exit 2 with readable variable/type error |
| `.venv/bin/ringlens schema init` | Exit 1 with actionable database connection error |
| `uv lock --check --offline` | Passed with local toolchain/cache paths |
| `git diff --check` | Passed |
| `.venv/bin/pytest tests/integration/test_falkordb.py --run-integration -k 'schema_init or refund_context' --tb=short` | Both selected tests failed during connection setup; no test graph created |
| `command -v docker` | Docker unavailable on PATH |

The integration attempt was repeated with approved network sandbox escalation
after a local socket probe returned EPERM. FalkorDB remained unreachable outside
the sandbox. The database failure is not counted as a successful integration check.

### Live database verification — user-reported success

After installing/starting Docker and configuring `.env`, the user supplied terminal
output showing the pinned image pulled, container healthy, doctor successful,
schema initialization successful twice, and all four integration tests passing.
These results were run by the user, not independently rerun by the coding agent.

| Command | User-reported outcome |
| --- | --- |
| `docker compose up -d --wait` | Image pulled; network and named volume created; container healthy |
| `.venv/bin/ringlens doctor` | Configuration valid, database reachable, FalkorDB commands available |
| `.venv/bin/ringlens schema init` (first run) | 20 indexes created, 0 already present; no records seeded |
| `.venv/bin/ringlens schema init` (second run) | 0 indexes created, 20 already present; no records seeded |
| `.venv/bin/pytest tests/integration --run-persistence` | All 4 tests passed in 0.43 seconds |

The passing integration suite verifies parameterized marker writes/reads,
server-enforced read-only queries, empty and repeatable schema initialization on a
disposable graph, preservation of existing test data, the unambiguous refund
context path, and cleanup of the owned test graphs. The persistence test verifies
AOF enabled with `everysec` fsync at the expected data directory, a changed server
identity after restart, and survival of its marker across that orderly restart.
This resolves the required Phase 1 startup/persistence and Phase 2 live schema
checks. It does not establish power-loss durability or backup recovery.

Native constraints remain source-verified only and deliberately not enabled;
indexes do not enforce uniqueness. Importer validation obligations remain as
documented. There are no remaining database blockers for the Phase 2 scope.

Commands for repeating the verification:

```sh
uv sync --locked
docker compose up -d --wait
uv run ringlens doctor
uv run ringlens schema init
uv run ringlens schema init
uv run pytest tests/integration --run-persistence
```

The integration suite owns UUID-named graphs and deletes only those graphs. No
application graph or named volume was deleted during Phase 2. Schema initialization
may create an empty graph and indexes, but never creates application, case, or
metadata records. Future work is generation and ingestion following the contracts;
full data, scoring, orchestration, API/dashboard work, and the LLM are not implemented.

---

## Phase 1 historical handoff

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

Historical note: the startup, doctor, graph, cleanup, and restart-persistence
blockers below were subsequently resolved by the user's successful live
verification recorded in the Phase 2 section above.

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
