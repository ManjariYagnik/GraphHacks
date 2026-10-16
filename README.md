# RingLens

RingLens investigates possible coordinated refund abuse by following accounts,
orders, devices, addresses, and refunds in FalkorDB. FalkorDB is the authoritative
runtime store and will determine which connections are discovered. All demo data
will be synthetic.

Phase 1 provides the Python package, configuration, graph connection, diagnostics,
and local database setup. Phase 2 adds strict records, cross-record validation,
the file contract, and additive schema initialization. Phase 3 adds deterministic
synthetic fixtures and file validation. Phase 4 adds validated, resumable FalkorDB
ingestion and graph verification. Phase 5 adds two device expansions, evidence
paths, and transparent v1 priority rules. The case milestone adds explicit case
creation, immutable assessment history, duplicate prevention, and saved-case readers.
The review workflow, LLM orchestration, API servers, and dashboards remain deferred.

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Docker with
Compose v2 (for example Docker Desktop). Run from the repository root:

```sh
uv python install 3.12
uv sync --locked
cp .env.example .env
openssl rand -hex 32
```

Paste the generated value into `RINGLENS_FALKORDB_PASSWORD` in `.env`. Keep `.env`
private; it is ignored by Git and Docker build contexts. The package reads `.env`
from the current directory; exported environment variables override it.

| Variable | Default | Meaning |
| --- | --- | --- |
| `RINGLENS_FALKORDB_HOST` | `127.0.0.1` | Database hostname or IP, without a URL |
| `RINGLENS_FALKORDB_PORT` | `6379` | TCP port, 1–65535; also the Compose host port |
| `RINGLENS_FALKORDB_PASSWORD` | unset | Secret; required by Compose; unset supports an existing unauthenticated server |
| `RINGLENS_GRAPH_NAME` | `ringlens_demo` | 1–128 characters: letters, digits, `_`, `.`, `-`; first character alphanumeric or `_` |
| `RINGLENS_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR, CRITICAL (case insensitive) |

Diagnostics do not print passwords, query text, parameter values, or raw server
errors. Avoid sharing `docker compose config` output because Compose expands secrets.

## Start and check

```sh
docker compose up -d --wait
uv run ringlens --help
uv run ringlens doctor
uv run ringlens schema init
```

Doctor validates settings and runs `PING`, `GRAPH.CONFIG GET TIMEOUT_DEFAULT`, and
`GRAPH.LIST`. It does not create a graph and succeeds before ingestion has run.
Invalid configuration exits 2; unavailable connectivity, authentication, or graph
commands exit 1 with guidance. Starting Docker requires a running Docker daemon.

`schema init` creates missing ID/time indexes, waits for them to become operational,
and can be rerun safely. It may create an empty graph but seeds no application or
case records. Indexes do not enforce uniqueness; the importer enforces
the documented rules. See [graph schema](docs/graph-schema.md) and
[data contract](docs/data-contract.md) for validation, relationships, and hashing.
Pure validation tests require neither a database nor a future generator.

`GraphStore` uses the [official FalkorDB Python client](https://github.com/FalkorDB/falkordb-py).
Use `store.read(cypher, params={...})` or `store.write(cypher, params={...})` within
`with GraphStore(Settings()) as store:`. Reads use `GRAPH.RO_QUERY`; writes use
`GRAPH.QUERY`. Pass values as `$parameters`, never interpolate evidence into Cypher.
The wrapper returns the client's `QueryResult`. Query deadlines default to 5,000 ms
and accept `timeout_ms=1..10000`; connection and socket deadlines are 3 and 12 seconds.
Failed writes are not automatically retried; a lost connection or timeout can leave
their outcome unknown.

## Import and inspect

Use the existing fixtures or generate them, then validate/import into the configured
graph. No evaluation file is read during ingestion:

```sh
uv run ringlens data generate --seed 42 --as-of 2026-10-15T10:00:00Z --output data/demo --evaluation-output evaluation/demo_ground_truth.json
uv run ringlens ingest --input data/demo/base --dry-run
uv run ringlens ingest --input data/demo/base
uv run ringlens graph verify
uv run ringlens graph stats
uv run ringlens graph paths --limit 5
uv run ringlens ingest --input data/demo/new_evidence --dry-run
uv run ringlens ingest --input data/demo/new_evidence
uv run ringlens graph verify
```

Base counts are 50 accounts, 42 devices, 46 addresses, 250 orders, and 42 refunds;
append increases refunds to 48 and advances the horizon to 2026-10-17 11:00 UTC.
READY reimports are verified no-ops. Dry-run creates no graph state or indexes.
Imports are resumable across queries; readers must require Dataset.status READY.
Retry an unfinished batch with its original input. Run one writer per graph.

Use `--graph NAME` on ingest, schema init, verify, stats, and paths for disposable
graphs. See [ingestion](docs/ingestion.md) for validation, metadata, failure/retry,
and genuine path query details. Ingestion preserves existing cases and reviews.

## Investigate

After a successful import, investigate a stored refund using graph relationships:

```sh
uv run ringlens investigate --refund-id REFUND_ID
uv run ringlens investigate --refund-id REFUND_ID --format json
uv run ringlens investigate --refund-id REFUND_ID --window-days 30 --as-of 2026-10-15T10:00:00Z
uv run ringlens investigate --refund-id REFUND_ID --create-case --format json
uv run ringlens cases list --format json
uv run ringlens cases show --case-id CASE_ID --format json
```

The default assessment time is the READY Dataset's imported horizon. Two device
expansions find candidate accounts; addresses provide supporting evidence.
Results include actual paths, deduplicated source records, deterministic evidence
IDs, and the five explicit v1 rule contributions. Priority is for demonstration,
not a probability or proof of abuse. Without `--create-case`, investigation writes
nothing. With it, COMPLETE MEDIUM/HIGH assessments create or update a case;
LOW creates no new case but may update an existing one. Incomplete assessments
never publish cases. Reviewer status and earlier assessments are preserved.

Group keys exclude the seed refund, so another seed with the same group/evidence
reuses the case and assessment. New input/window/scoring configuration appends an
assessment; a changed group gets its own case and overlap review flags. CLOSED
cases stay CLOSED. Case readers show only completed assessments and use the latest
assessment's score; historical evidence contributions are never added together.
Rerun `schema init` to add five indexes (25 total), without removing data or indexes.

Use `--graph NAME` for disposable graphs. `--max-accounts` (100) and
`--max-evidence` (2000 distinct paths, also a per-query row bound) limit work.
Truncation or a failed query returns INCOMPLETE without a score/priority and exits 3;
missing/non-READY data exits 1 and invalid inputs exit 2. `--display-paths` caps
text rendering only, after analysis, and discloses omitted paths. JSON remains full.
See the human-only [investigation guide](docs/investigation.md) for actual generated
refund IDs and rules, and the [case guide](docs/cases.md) for a complete demo,
identities, completion/retry semantics, and interfaces for later agent tools.

## Durability and shutdown

Compose pins released
[`falkordb/falkordb-server:v4.20.7`](https://github.com/FalkorDB/FalkorDB/releases/tag/v4.20.7)
by its multi-architecture digest. Only database port 6379 is published, on loopback.
The named volume `ringlens_falkordb_data` mounts the
[documented data directory](https://docs.falkordb.com/operations/durability/persistence),
`/var/lib/falkordb/data`. The released
[Dockerfile](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/build/docker/Dockerfile.server)
and [startup script](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/build/docker/run.sh)
confirm the module and data paths. Compose starts that module directly with quoted
password arguments, avoiding the startup script's unquoted `REDIS_ARGS` splitting.

Durability is explicitly configured: AOF enabled, `appendfsync everysec`, RDB
snapshots after 60 seconds with at least one change, and `noeviction`. A volume alone
does not cause writes to reach disk. The every-second AOF policy balances demo
performance and durability; a crash can lose approximately the last second of
writes. See the [official durability guide](https://docs.falkordb.com/operations/durability).
The restart test covers an orderly restart, not power-loss recovery or backups.

Stop and remove containers while preserving the named volume:

```sh
docker compose down
```

Run `docker compose up -d --wait` to resume. Never use `down -v` or remove the named
volume if you want to retain data.

## Verification

```sh
uv lock --check
uv run python -c 'import ringlens; print(ringlens.__version__)'
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run pytest tests/integration --run-integration
uv run pytest tests/integration --run-persistence
uv run pytest tests/integration/test_ingest.py --run-integration
uv run pytest tests/integration/test_cases.py --run-integration
```

Generate and validate the synthetic fixtures without a database:

```sh
uv run ringlens data generate --seed 42 --as-of 2026-10-15T10:00:00Z --output data/demo --evaluation-output evaluation/demo_ground_truth.json
uv run ringlens data validate --input data/demo/base
uv run ringlens data validate --input data/demo/new_evidence --base data/demo/base
```

Same inputs reproduce identical files; differing files require `--overwrite` or a
new output path. Base has 50 accounts, 250 orders, and 42 refunds; the append adds
six refunds only. The [human demo guide](docs/demo-scenarios.md) describes scenarios
and starting refund IDs. It and `evaluation/` are excluded from Docker contexts;
neither may become runtime agent input.

Default pytest runs unit tests and skips database tests. Explicit integration runs
fail if FalkorDB is unavailable. Each database test uses its own UUID-named graph,
checks a parameterized marker round trip, and deletes only its own graph. Schema
checks initialize twice, preserve existing test data, and verify the refund context
path using a minimal disposable fixture. The persistence option additionally
restarts the local Compose database, checks its server identity changed and the
marker survived, then cleans up its test graph.
It briefly interrupts all clients of that local service. Run it without concurrent
database work. No test deletes the application graph or a volume.

`evaluation/` and nested evaluation directories are excluded by `.dockerignore`;
future runtime images must keep that exclusion so scenario labels cannot become
agent inputs. No evaluation data is loaded by the package. See
[implementation status](docs/implementation-status.md) for checks actually run.
