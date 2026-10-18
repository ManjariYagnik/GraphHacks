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
Persistent scoped human reviews and a native tool-calling OpenAI or Claude agent
are available. The local Streamlit dashboard adds CSV upload, graph-derived
insights, investigation paths, cases and reviews. No API server is required.

## How the graph drives an investigation

```mermaid
flowchart LR
    Account -- PLACED --> Order
    Order -- USED_DEVICE --> Device
    Order -- SHIPPED_TO --> Address
    Account -- REQUESTED_REFUND --> Refund
    Refund -- FOR_ORDER --> Order
    Investigation -- INCLUDES --> Account
    Investigation -- HAS_ASSESSMENT --> Assessment
    Assessment -- HAS_EVIDENCE --> Evidence
    Investigation -- HAS_REVIEW --> Review
    Review -- REVIEWS_ASSESSMENT --> Assessment
    Review -- ABOUT --> Evidence
```

Starting from a refund, RingLens queries FalkorDB for its requesting account,
order, device and shipping address. It then follows `Account → Order → Device
← Order ← Account` for two in-window expansions, preserving the returned paths.
Orders and refunds for the discovered accounts supply the timestamps and amounts
used by deterministic scoring rules. Addresses support evidence but do not drive
account discovery. The agent calls typed tools to obtain this graph-backed
assessment, prior human reviews, and the policy-controlled case action; it does
not invent edges or calculate the official score. See [graph schema](docs/graph-schema.md),
[investigation queries](src/ringlens/investigation_queries.py), and
[agent tools](src/ringlens/agent_tools.py).

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
cases reopen only for material new unexplained qualifying evidence; unchanged
reviewed facts preserve the reviewer decision. Case readers show only completed
assessments and use the latest assessment's score; historical evidence contributions
are never added together. Rerun `schema init` to add five review/agent indexes
(30 total), without removing data or indexes.

Use `--graph NAME` for disposable graphs. `--max-accounts` (100) and
`--max-evidence` (2000 distinct paths, also a per-query row bound) limit work.
Truncation or a failed query returns INCOMPLETE without a score/priority and exits 3;
missing/non-READY data exits 1 and invalid inputs exit 2. `--display-paths` caps
text rendering only, after analysis, and discloses omitted paths. JSON remains full.
See the human-only [investigation guide](docs/investigation.md) for actual generated
refund IDs and rules, and the [case guide](docs/cases.md) for a complete demo,
identities and completion/retry semantics. The [review guide](docs/reviews.md) and
[agent guide](docs/agent.md) cover persistent memory, native tools, and full demos.

## Human reviews and investigation agent

Review an existing complete assessment explicitly, even when LOW creates no case:

```sh
uv run ringlens reviews record --refund-id ref_4d44e5cc3e0a8dae77fd --reviewer-id demo-reviewer --outcome BENIGN_EXPLANATION --explanation "Shared device and address explained; future events are not cleared." --rule R1 --rule R5
uv run ringlens reviews history --refund-id ref_4d44e5cc3e0a8dae77fd
```

Run this before importing new evidence to reproduce the base-household demo. Review
writes recompute canonical graph evidence, save an immutable snapshot without
manufacturing a case, and append a scoped decision. Corrections use `--supersedes`;
case closure requires explicit `--case-id` and `--case-status CLOSED`. A benign
explanation applies only to reviewed facts. Scores remain unchanged. New household
bursts after append remain unexplained and qualify for case creation. See the
[review guide](docs/reviews.md) for the fresh-graph sequence and closure/reopen policy.

To use Claude API credits, set these values in your ignored `.env`, then run
`uv sync --locked`:

```dotenv
RINGLENS_AGENT_PROVIDER=anthropic
RINGLENS_ANTHROPIC_API_KEY=your-anthropic-api-key
RINGLENS_AGENT_MODEL=claude-sonnet-4-6
```

Use a tool-calling Claude model available in your Anthropic account. The official
Anthropic SDK uses native Messages tool_use/tool_result calls. ANTHROPIC_API_KEY
is accepted as a fallback for the key. Only the selected provider is called;
OpenAI credentials/credits are unnecessary when using anthropic.

Existing OpenAI setups continue to work with `RINGLENS_AGENT_PROVIDER=openai`
(the default), `RINGLENS_OPENAI_API_KEY` (or `OPENAI_API_KEY`), and a corresponding
Responses model in `RINGLENS_AGENT_MODEL`. There is no assumed model or automatic
fallback between providers. Missing configuration fails before a provider request.

```sh
uv run ringlens agent run --message "Explain refund ref_2b42ec18e21a70bdfa15" --mode assess
uv run ringlens agent run --message "Investigate refund ref_4d44e5cc3e0a8dae77fd and open a case if needed." --mode investigate --format json
uv run ringlens agent show-run --run-id RUN_ID
```

Default assess mode allows analysis/evidence/review/case lookup and enforces a
case-write prohibition in code; it saves AgentRun audit only. Investigate mode
authorizes deterministic case policy without repeated confirmation. Human reviews
are never an agent tool. Grounded final responses name confirmed evidence, sources,
reviews and case actions. Fresh sessions retrieve memory from FalkorDB. The
[agent guide](docs/agent.md) documents tools, bounds, credentials, setup and the
initial-review/restart/append demonstration. Provider mocks exercise orchestration;
the user has verified OpenAI and Anthropic read-only runs. Claude SDK/orchestration
are also tested with mocked HTTP and real graph tools; paid live calls remain opt-in.

## Local Streamlit dashboard

Start FalkorDB, install locked dependencies, and open the private dashboard:

```sh
uv sync --locked
docker compose up -d --wait
uv run ringlens doctor
uv run streamlit run src/ringlens/streamlit_app.py --server.address 127.0.0.1
```

Visit `http://127.0.0.1:8501`. The five sections are Overview, Upload Data,
Investigate, Cases & Reviews, and Import History. Upload accepts entity CSVs,
offers column mapping and bounded previews, validates the whole batch, then
imports only on an explicit click. Downloadable templates and full synthetic
base/append CSV bundles are in the UI; committed copies are in
[`data/demo_csv/base`](data/demo_csv/base) and
[`data/demo_csv/new_evidence`](data/demo_csv/new_evidence). No LLM key is needed
for upload, graph metrics, deterministic scoring, case policy or human review.
The light violet/teal theme, labelled status badges, and graph entity legend are
configured in `.streamlit/`; see the [frontend guide](docs/frontend.md).

For a first run, upload the five base CSVs, choose **Create new dataset**, and
set Data through to `2026-10-15T10:00:00Z`. Validate, import, then choose the
new graph in the sidebar. Overview shows 50 accounts, 250 orders and 42 refunds;
42/250 in-window orders were refunded (16.8%). Assess
`ref_9082163771ef0a5e5b26` to see a 90/HIGH demonstration priority and a
two-hop bridge path. Use **Investigate and apply case policy** to request a
case without an LLM. To show the full tool-calling workflow in the UI, use
**Run agent and apply case policy** on the Investigate page: the configured
provider requests graph analysis and review context, and the backend confirms
the case action. **Ask agent · read-only** gives a paid explanation without a
case write. The panel shows run status, tool activity, grounded evidence and
confirmed case ID. Repeated requests reuse the completed case/assessment.
For the household review-memory example, assess `ref_4d44e5cc3e0a8dae77fd`,
explicitly save a
human review of R1/R5, then upload the refund-only append CSV at
`2026-10-17T11:00:00Z`. Earlier review history remains visible beside new
refund-burst evidence. Agent calls use the selected graph and spend provider
credits only when a form button is submitted; deterministic analysis stays usable
without an LLM key.

CSV IDs, timestamps, INR paise, limits, errors, and mappings are documented in
the [CSV contract](docs/csv-format.md). The [frontend guide](docs/frontend.md)
documents graph selection, import retry, inclusive metrics, path visualization,
and the local/private scope. Keep the app bound to loopback; dataset selection
does not provide multi-user authentication.

## Five hackathon example workflows

The [example guide](example/README.md) packages five validated synthetic
workflows: an ordinary refund, shared household resources, a one-device cluster,
a two-device bridge, and later evidence after an explicit human review. The
[4–5 minute demo script](example/demo/demo_script.md) selects the bridge case
because FalkorDB's second device expansion is essential to discover all five
accounts. Run it without a provider key:

```sh
uv run python example/demo/demo.py --example 04 --dry-run
uv run python example/demo/demo.py --example 04
```

The live runner creates a new disposable graph, imports and verifies source
records, investigates the seed, applies the existing case policy, and reads the
case back through a new database connection. `--with-agent` is an explicit paid
option for a live read-only tool-calling explanation; no provider request occurs
by default. The runner preserves the graph for UI/CLI inspection and never
adopts or deletes an existing graph. See [measured output](example/demo/expected_output.md)
and the [frontend guide](docs/frontend.md) for how to select that graph.

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
uv run pytest tests/integration/test_reviews.py tests/integration/test_agent.py --run-integration
uv run pytest tests/unit/test_csv_adapter.py
uv run pytest tests/integration/test_dashboard.py --run-integration
# Optional live provider smoke, after configuring key/model (may incur charges):
uv run pytest tests/integration/test_agent_live.py --run-integration --run-live-agent
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
