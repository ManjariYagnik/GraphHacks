# RingLens

RingLens will investigate possible coordinated refund abuse by following accounts,
orders, devices, addresses, and refunds in FalkorDB. FalkorDB is the authoritative
runtime store and will determine which connections are discovered. All demo data
will be synthetic.

Phase 1 provides the Python package, configuration, graph connection, diagnostics,
and local database setup. Phase 2 adds strict records, cross-record validation,
the file contract, and additive schema initialization. Generation and ingestion
remain deferred, along with scoring, orchestration, API servers, and dashboards.

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
case records. Indexes do not enforce uniqueness; the future importer must enforce
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
```

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
