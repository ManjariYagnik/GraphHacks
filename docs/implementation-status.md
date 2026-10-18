# RingLens implementation status

## Dashboard visual refinement

Updated: 2026-10-18 (Asia/Kolkata).

Refined the existing Streamlit frontend without changing graph services, CSV
validation, imports, scoring, cases, or reviews. `.streamlit/config.toml` sets
the light violet/teal theme with accessible text and table-header colours;
`.streamlit/style.css` handles bounded content width, sidebar selection, card
spacing, metrics, upload steps, tables, buttons, focus and narrow-screen layout.
All five screens now have consistent headings and descriptions. Overview has
coloured metric icons and a teal refund-activity chart. Upload has a tinted
introduction, four-step workflow and labelled validation count. Investigation
has a priority badge and stronger selected-path emphasis; the graph's entity
palette avoids risk-status orange/red. Case/review and import screens keep their
existing explicit-write controls and backend results. Existing worktree changes
from the prior frontend milestone were preserved.

Official Streamlit [theming](https://docs.streamlit.io/develop/concepts/configuration/theming),
[theme configuration](https://docs.streamlit.io/develop/api-reference/configuration/config.toml),
and [bar chart](https://docs.streamlit.io/develop/api-reference/charts/st.bar_chart)
documentation were checked. `streamlit config show` accepted the configured
theme. `.venv/bin/pytest tests/unit/test_csv_adapter.py
tests/integration/test_dashboard.py --run-integration -q --tb=short` passed
**13 tests**. The full `.venv/bin/pytest --run-integration -q --tb=short` run
passed **388 tests**, with 2 opt-in tests skipped (paid provider and database
restart). `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, and
`git diff --check` passed. The initial sandboxed
integration attempt could not access localhost despite a healthy container;
rerunning with localhost access passed on disposable graphs. No browser binary
or Playwright installation was available, so a pixel-level visual review was
not performed here. A temporary loopback Streamlit server started and its
`/_stcore/health` endpoint returned `ok`; the server was then stopped.

---

## Local Streamlit CSV-to-insights milestone

Updated: 2026-10-18 (Asia/Kolkata).

Implemented a working local/private Streamlit dashboard without an API server.
Five sections: Overview, Upload Data, Investigate, Cases & Reviews, Import History.
FalkorDB remains the runtime truth; no evaluation file or LLM is used for import,
metrics or deterministic scoring. The earlier Anthropic early-stop memory-lookup
fix in `agent.py` and its regression test were preserved.

New modules: `csv_adapter.py` enforces bounded CSV parsing, mapping, strict-model
conversion, physical-row errors, canonical batches and template/sample ZIPs;
`dashboard_data.py` keeps a local allowlisted graph registry, revalidates before
single-writer import, and queries graph metrics/scans; `graph_view.py` projects
actual path nodes/edges into a bounded interactive display; `streamlit_app.py`
renders the five workflows and delegates writes to existing ingestion/case/review
services. `.ringlens/datasets.json` is ignored by Git/Docker. Source schema,
JSONL/manifest contract, score rules and case policy were not changed. Server
limits are configurable through `RINGLENS_CSV_MAX_*` in `.env.example`.

The committed synthetic CSV examples in `data/demo_csv/base/` contain 50 accounts,
42 devices, 46 addresses, 250 orders and 42 refunds; the refund-only append CSV
contains six rows. The Overview's inclusive 30-day refunded-order percentage on
base data is 42/250 = 16.8%. A sample graph investigation of
`ref_9082163771ef0a5e5b26` found depth-two accounts and genuine path edges.
No case or review mutation occurs during preview, navigation, read-only
assessment, or deterministic batch scanning.

Official Streamlit [installation](https://docs.streamlit.io/get-started/installation/command-line),
[file uploader](https://docs.streamlit.io/develop/api-reference/widgets/st.file_uploader),
[forms](https://docs.streamlit.io/develop/api-reference/execution-flow/st.form_submit_button),
[ECharts graph](https://docs.streamlit.io/develop/api-reference/charts/st.echarts_chart),
and [AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest)
documentation were checked. `uv add 'streamlit>=1.62,<2'` resolved and locked
Streamlit 1.66.0 (65 total locked packages). The initial sandbox-only network
attempt failed DNS; the authorized dependency install succeeded with network
access. An initial localhost server start was blocked by sandbox socket policy;
the same loopback-only start succeeded with local socket access.

Checks actually run:

- `UV_CACHE_DIR=.tools/uv-cache .tools/uv-package/bin/uv lock --check --offline`:
  passed.
- `.venv/bin/pytest tests/unit/test_csv_adapter.py -q --tb=short`: 9 passed.
- `.venv/bin/pytest tests/integration/test_dashboard.py --run-integration -q --tb=short`:
  4 passed on disposable graphs, including real CSV base/append imports,
  graph metrics, invalid/no-write inputs, multi-hop paths, scoped household review
  surviving the append, later qualifying case creation, and Streamlit AppTest
  rerenders without repeated writes.
- `.venv/bin/pytest --run-integration -q --tb=short`: 388 passed, 2 skipped
  (explicit paid live agent and persistence-restart tests) in 25.37 seconds.
- Focused Streamlit investigate-screen AppTest rerun after the final test extension:
  1 passed, including the HIGH score and interactive chart rendering path.
- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed,
  64 Python files formatted.
- `.venv/bin/ringlens --help` and importing Streamlit 1.66.0: passed.
- `.venv/bin/streamlit run src/ringlens/streamlit_app.py --server.address 127.0.0.1
  --server.port 8519 --server.headless true --browser.gatherUsageStats false`:
  started on loopback; `curl -fsS http://127.0.0.1:8519/_stcore/health`
  returned `ok`; server was stopped cleanly.

The full manual browser walkthrough and accessibility review were not available
from this tool environment. AppTest simulates UI navigation but not browser
rendering. No paid provider call or database restart was made for this milestone.
The importer remains a single-writer multi-query operation; a crash may leave
FAILED/LOADING and requires retry of the original batch. Dataset selection is
not authentication. The frontend is intentionally bound to loopback.

---

## Claude provider support

Updated: 2026-10-17 (Asia/Kolkata).

Added explicit `RINGLENS_AGENT_PROVIDER=anthropic` support using the official
Anthropic Python SDK, locked at **0.125.0**. The default stays openai for existing
setups. No automatic provider fallback occurs. Claude uses the same five domain
tools, native tool calling, modes, graph investigation, scoring, scoped memory,
case policy, completion/revision checks, and grounded final rendering.

Inspected applicable instructions (no AGENTS.md found), README/status, provider,
runner, CLI, settings/lockfile, and existing unit/integration tests. The 276-test
unit baseline passed before code changes. Earlier milestone changes were already
staged; their staging/work was preserved, with no commit or .env change.

Verified current official Anthropic documentation before implementing:
[SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python),
[client tool results](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls),
[strict tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use),
[serial control](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use),
and [JSON schema transformation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).
The adapter translates native tool_use blocks into the runner's existing call
format and sends each matching tool_result in the following user message.
Consecutive results are grouped, errors marked, native assistant blocks preserved,
and incomplete/refused replies rejected. Unsupported grammar constraints are
transformed with the official SDK; full strict arguments remain validated locally.
Transport timeout and one retry are preserved. Credentials/raw provider errors
never enter responses/audit. Case/review/scoring/ingestion/schema code is unchanged.

Files: src/ringlens/agent_provider.py adds secret Anthropic settings, provider
selection/factory and adapter; cli.py delegates to the factory. pyproject.toml and
uv.lock add Anthropic plus docstring-parser (38 total locked packages).
.env.example, README and docs/agent.md describe both providers. New
unit/test_anthropic_provider.py covers real SDK/mock HTTP native continuation,
strict-schema transformation, stop reasons, redaction, no fallback, configuration,
and error result grouping. integration/test_agent.py adds two real SDK/mock HTTP +
real FalkorDB workflows (assess creates no case; investigate creates a genuine case).
The opt-in live test now uses the selected provider; existing offline OpenAI tests
explicitly select openai so they cannot accidentally consult a Claude key.

Commands/outcomes:

- `uv add 'anthropic>=0.75,<1'`: installed 0.125.0 with approved network access;
  lower bound subsequently set to 0.125, the tested version exposing transform_schema.
- `uv lock --offline` and `uv lock --check --offline`: passed.
- `.venv/bin/pytest tests/unit/test_agent_provider.py tests/unit/test_anthropic_provider.py -q --tb=short`:
  **23 passed** (13 existing + 10 new).
- `.venv/bin/pytest --run-integration -q --tb=short`:
  **374 passed, 2 skipped in 23.38 seconds** (live provider and explicit restart test).
  This covers all 286 unit and 88 real-database tests. No volume restart was needed
  for a provider-only change; earlier milestone restart persistence remains recorded below.
- `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`: passed, 56 files.
- Key-presence-only check: Claude key absent; selected provider currently openai.
  No secrets were printed and no live provider credits were spent.

User-supplied logs separately confirm a successful live **OpenAI** read-only run,
run_24e446e28ee2402aa8a3c9eb759ace56, score 35 LOW. That does not verify Claude.
The Anthropic SDK tests use mocked HTTP, while graph services are real. Live Claude
compatibility remains unverified until the user configures their key/model.

Set these in ignored .env (leave database settings intact):

```dotenv
RINGLENS_AGENT_PROVIDER=anthropic
RINGLENS_ANTHROPIC_API_KEY=your-anthropic-api-key
RINGLENS_AGENT_MODEL=claude-sonnet-4-6
```

ANTHROPIC_API_KEY is also accepted. Use a model available to the account; no model
is assumed by code. Then:

```sh
uv sync --locked
uv run ringlens agent run --message "Explain refund ref_4d44e5cc3e0a8dae77fd" --mode assess
# Optional dedicated live smoke on an owned disposable graph:
uv run pytest tests/integration/test_agent_live.py --run-integration --run-live-agent
```

If uv is not on PATH, use the local exports documented below. No OpenAI credits
are required when anthropic is selected; no graph/schema migration is required.

---

## Persistent reviewer memory and tool-calling agent milestone

Updated: 2026-10-17 (Asia/Kolkata).

Implemented through the human reviewer CLI and official OpenAI native tool-calling
adapter. Existing graph discovery, v1 scoring, source/file contracts, fixture bytes,
ingestion, case identity and immutable history were preserved. No dashboard or
API server was added. No applicable AGENTS.md or preconfigured LLM provider was
found. OpenAI Docs skill instructions and official Responses/function-calling/Python
SDK documentation were read before implementing the provider. No subagents were used.

### Inspection and baseline checks

Read README, implementation status, graph/data-contract/investigation/case/scenario
guides, source/assessment models, configuration, graph wrapper, ingestion, manifests,
and existing tests. Before extension:

- `.venv/bin/pytest tests/unit -q`: **263 passed**.
- `.venv/bin/pytest tests/integration --run-integration -q --tb=short`:
  **65 passed, 1 persistence test skipped** without its explicit restart option.
- Ruff passed. No source data, manifests, scenario truth, traversal queries or
  scoring arithmetic were changed to accommodate this milestone.

### Files and additive changes

| Files | Responsibility |
| --- | --- |
| `src/ringlens/review_models.py` | Strict ScopedReview/ReviewRequest/EvidenceFact/ReviewContext, canonical JSON scope and fact-hash validation; legacy Review remains unchanged |
| `src/ringlens/review_queries.py` | Fixed parameterized review/standalone-assessment staging, scoped links, corrections and guarded completion |
| `src/ringlens/reviews.py` | Canonical assessment recomputation, append-only/retry-safe human reviews, snapshot persistence without a case, deterministic scope matching and material-change comparison |
| `src/ringlens/case_models.py`, `case_queries.py`, `cases.py` | ALREADY_REVIEWED/REOPENED actions, independent memory checks before case publication, preservation of reviews/overlap policy, READY standalone-snapshot attachment and attachment completion markers |
| `src/ringlens/agent_tools.py` | Five strict native tool schemas, server-issued references/validated stored snapshots, code-enforced modes, bounded results and explicit errors |
| `src/ringlens/agent_provider.py` | OpenAI Responses function calls, secret credentials/model settings, timeouts, one transport retry, missing-configuration/provider errors |
| `src/ringlens/agent.py` | Bounded tool loop, cached call IDs, deterministic grounded final output, sanitized AgentRun audit and fresh-process run lookup |
| `src/ringlens/cli.py` | reviews record/history; agent run/show-run; thin delegation to domain services |
| `src/ringlens/schema.py`, `verify.py` | AgentRun label, two review relation signatures, five additional range indexes, AgentRun stats count |
| `.env.example`, `pyproject.toml`, `uv.lock` | Optional provider variables and official openai SDK, locked at 2.54.0; Python 3.12 retained |
| `tests/unit/test_agent_provider.py` | 13 offline strict-tool/settings/review/fingerprint tests, including real SDK with fake HTTP transport |
| `tests/integration/test_reviews.py` | 11 real-graph review, CLI, scope/correction, restart, retry, append/reopen and 25 → 30 index upgrade checks |
| `tests/integration/test_agent.py` | 10 mocked-provider/real-graph tool-orchestration tests, including read-only permissions, stale/capped analysis, fresh-session memory, duplicate prevention, CLOSED reopening and historical evidence lookup |
| `tests/integration/test_agent_live.py`, `tests/conftest.py` | Explicit --run-live-agent smoke, never silently substituted by a mock; still requires integration access |
| `tests/integration/test_cases.py` | Existing acceptance behavior preserved; index assertions updated to additive 30-index schema (also verifies 20 → 30) |
| README, `docs/reviews.md`, `docs/agent.md`, schema/case/investigation guides | Setup, commands, human/demo workflow, decisions, interfaces, limitations |
| `.dockerignore` | Excludes new human scenario guides; evaluation/ remains excluded |

Schema now has **12 labels, 15 relationship types / 16 signatures, 30 range
indexes**. New signatures: Review REVIEWS_ASSESSMENT Assessment and Review
SUPERSEDES Review. Review adds reviewer, assessment/evidence scope/fingerprints,
optional case/validity/supersedes, and LOADING/READY metadata through ScopedReview.
AgentRun stores sanitized runtime audit. No source-model/schema-version migration
is needed. `schema init` adds missing indexes only; 25 → 30 and repeat-zero behavior
are verified without altering existing records. Native uniqueness guarantees are
not newly claimed.

HAS_ASSESSMENT now has a separate LOADING/READY attachment marker. Existing links
without the property are compatible as READY. This fixes the failure/retry edge
case where a completed standalone review snapshot is attached to an existing
published case: the prior completed history remains readable until final case
publication succeeds, and retry safely completes without duplicate snapshots.

### Decisions and observed behavior

Reviews are explicit human-only writes, recomputed from canonical graph state,
never model-authored outcomes. Identical intent/assessment/scope hashes return the
same completed review without editing it. Corrections append SUPERSEDES, retain
history and exact original fact scope, and reject branches. Optional validity is
inclusive by assessment event time. Legacy unscoped reviews remain readable but
provide no automated clearance. Reviewer identifiers are local caller assertions;
this milestone adds no identity/authentication service.

Fact fingerprints separate actual rule/version/source records/measurements from
assessment time, input revision and assessment-specific evidence IDs. Matching
benign scope is limited to the same anchor and reviewed records (including a
narrowed subset aging out of the window); new transactions, account membership,
bursts and increased global device use are not cleared. NEEDS_MORE_INFORMATION
is consideration, never benign clearance. Reviewing one rule does not suppress
another. Numeric scores remain unchanged.

| Synthetic request | Observed rules/score | Disposition |
| --- | --- | --- |
| Pattern A, ref_2b42ec18e21a70bdfa15 | R1–R4, 90 HIGH, four accounts | CREATED; repeat/another seed EXISTING_UNCHANGED |
| Pattern B, ref_9082163771ef0a5e5b26 | R1–R4, 90 HIGH, five accounts including depth 2 | Existing traversal/case acceptance passes, CREATED/reuse |
| Base household, ref_4d44e5cc3e0a8dae77fd | R1/R5, 35 LOW | NO_CASE_REQUIRED before review |
| Household after scoped benign review | Unchanged 35 LOW | ALREADY_REVIEWED; standalone assessment/evidence/review, zero cases |
| Household after six appended refund events | R1–R5, 100 HIGH; two new bursts, three accounts at 3/5 refunds | CREATED; repeat EXISTING_UNCHANGED; old R1/R5 review retained |
| Separate CLOSED household fixture with qualifying new evidence | 100 HIGH | REOPENED same case, prior review/assessments retained |

The CLOSED fixture uses an explicit test-only base scoring configuration to create
a MEDIUM case, then a human review explicitly closes it. Default LOW never creates
an artificial escalated case. Unchanged A evidence after an unrelated import/clock
change remains explained. LOW and incomplete new results never reopen cases.

Case persistence rechecks memory itself under the shared graph writer lease and
again before final publication, alongside existing READY/revision/completeness
checks. Agent context is advisory to the LLM; it cannot bypass domain policy.
Overlapping group handling is preserved; cases are not silently merged.

Five tools: analyze_refund, get_evidence, get_review_context, apply_case_policy,
get_case. Inputs cannot include an authoritative score/assessment JSON payload.
Session references are application-issued; stored asm_ references must resolve
to validated READY snapshots. Current memory/action requests reject stale revision.
Historical evidence lookup remains read-only and can explain old case snapshots.
Assess mode forbids all case mutations in code; only AgentRun audit is written.
Human review mutation is absent from the tool allowlist. Investigate mode authorizes
policy-controlled case writes without repeated approval.

Final responses render confirmed records, paths, rule contributions, review IDs,
reviewed scope, new/unexplained evidence, and actual case actions. Unsupported
provider prose is discarded. The heuristic score is not proof of wrongdoing.
Review notes/source strings are untrusted data. SDK encrypted reasoning stays only
in RAM for stateless continuation and is never retained as audit. AgentRun saves
minimal names/status/IDs/action summaries/errors/final text, not raw messages,
credentials, raw tool arguments or provider reasoning. A crash-left RUNNING is not
success. Audit failure after a write reports uncertainty rather than denying a
possibly committed action.

### Exact commands run and actual outcomes

Local uv remains at `.tools/uv-package/bin/uv`; set UV_CACHE_DIR=.tools/uv-cache
and UV_PYTHON_INSTALL_DIR=.tools/python when using that installation. Ordinary user
commands use `uv` on PATH. Network/socket approvals were used for dependency install
and real local database checks. Tests clean up only their UUID-named graphs.

| Command | Actual result |
| --- | --- |
| `uv add 'openai>=2,<3'` (local uv with approved network) | Installed official openai 2.54.0; lock resolves 36 packages |
| `uv lock --check --offline` | Passed |
| `uv sync --locked --offline` | Passed; editable package rebuilt/installed |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/ruff format --check .` | Passed, 55 files formatted |
| Imports of agent/provider/tools/reviews; `ringlens --help`, `reviews record --help` | Passed; CLI exposes reviewer/agent commands |
| `.venv/bin/ringlens agent run --message 'Explain refund ref_4d44e5cc3e0a8dae77fd'` | Expected exit 1 with actionable missing-key/model message; no live call |
| `.venv/bin/pytest tests/unit/test_agent_provider.py -q --tb=short` (initial 12-test version) | 12 passed; fallback test added subsequently and passes in final suite |
| `.venv/bin/pytest tests/integration/test_cases.py tests/integration/test_reviews.py tests/integration/test_agent.py --run-integration -q --tb=short` (before final CLI test) | 41 passed |
| `.venv/bin/pytest --run-persistence -q --tb=short` (final current code) | **363 passed, 1 live-provider test skipped, 22.73 seconds**: 276 unit + 87 real-database tests; includes orderly restart persistence |
| `.venv/bin/pytest tests/integration/test_agent_live.py --run-integration --run-live-agent -q` | **1 skipped**: provider key/model absent, no live request performed |
| `.venv/bin/ringlens doctor` after restart suite | Passed: valid configuration, reachable database, FalkorDB commands available |
| `git diff --check` | Passed |

An initial full run failed one newly added CLI test due to a missing json test
import (361 passed, one failed). The import was corrected and final runs pass.
This was a test defect, not a database verification blocker. No application graph
or named volume was deleted/reset. Persistence restarts retained the volume and
removed only the dedicated test graph afterward.

Provider tests are separated: official SDK uses a fake HTTP transport offline;
scripted native ProviderTurn mocks call real graph/domain services in integration.
These do not verify real model selection or live account/API compatibility.

### Setup, demo and next milestone

Run `uv sync --locked`, `docker compose up -d --wait`, `ringlens schema init`,
then follow the fresh-graph sequences in [reviews.md](reviews.md) and
[agent.md](agent.md). They include actual opaque refund/case IDs, initial analysis,
explicit human review, fresh-process lookup, append and changed case disposition.
Optional provider variables are listed in .env.example: RINGLENS_OPENAI_API_KEY
(or OPENAI_API_KEY), RINGLENS_AGENT_MODEL, RINGLENS_AGENT_TIMEOUT_SECONDS (30),
RINGLENS_AGENT_MAX_TURNS (8), RINGLENS_AGENT_MAX_TOOL_CALLS (16).

**Remaining unverified behavior:** no real provider smoke was possible without
credentials/model. The real SDK adapter is implemented, but no live LLM outcome
is claimed. Run the opt-in live command above after configuring the ignored .env.
No other required local database or offline check remains blocked. Concurrency,
power-loss recovery, provider model/account compatibility and real-world detection
accuracy are not claimed. Single-writer/completion-marker limitations persist.

The Streamlit milestone can consume AgentTools.execute/tool_schemas,
run_agent/get_agent_run, investigate, compare_evidence, record_review(ReviewRequest),
review_history, apply_case_policy, get_case and list_cases. Keep human review/status
controls outside the LLM tool allowlist. Separate audit writes from read-only case
permissions; disclose incomplete results and display omissions. The dashboard is
not implemented here. No commit was requested or created.

---

## Traversal, scoring, and case-creation milestone

Updated: 2026-10-16 (Asia/Kolkata).

Complete through working case creation and immutable assessment history. The
existing Phase 5 read-only investigation and v1 arithmetic were reused, not replaced.
The canonical source models/IDs/amounts/timestamps, fixture bytes/manifests,
ingestion behavior, and dependency lockfile remain unchanged. No applicable
AGENTS.md was found. Existing work was preserved; no commit was requested.
LLM orchestration, review submission/lifecycle policy, automatic reopening, and
the dashboard remain deferred.

### Inspection and previous verification

Read README, this status, graph/data-contract/investigation/scenario documentation,
source and metadata models, graph wrapper, schema initialization, manifests,
ingestion/verification code, and existing tests. Before extending them:

- `.venv/bin/pytest tests/unit -q`: **245 passed**.
- `.venv/bin/pytest tests/integration --run-integration -q --tb=short`:
  **44 passed**, one restart test skipped without its explicit persistence option.
- `.venv/bin/ruff check .`: passed.

Database checks used the running pinned FalkorDB container with approved sandbox
network/socket access. Source/case mutations and cleanup targeted owned UUID-named
disposable graphs only. The application graph and named volume were not reset or
loaded with test records. The final persistence run briefly restarted the shared
local database while preserving its volume.

### Files and service responsibilities

- `src/ringlens/case_models.py`: strict CaseRecord extension of Investigation,
  Assessment completion/snapshot model, CaseAction, and published reader DTOs.
  Original Investigation, Evidence, Review, and source models remain unchanged.
- `src/ringlens/case_queries.py`: fixed parameterized staging, immutable MERGE,
  endpoint/evidence verification, overlap flags/links, final publication, and readers.
- `src/ringlens/cases.py`: deterministic group/assessment identity, complete-result
  arithmetic/evidence validation, current-input guards, case policy, idempotent
  publication/recovery, immutable history, compatible evidence, and review readers.
- `src/ringlens/investigator.py`: seed-independent evidence fingerprints, a shared
  pre-persistence READY/revision guard, complete seed-to-candidate path witnesses,
  and a separate text-only display cap. Discovery and scoring semantics stay v1.
- `src/ringlens/investigation_models.py`: complete semantic candidate seed paths
  and explicit per-rule thresholds/available points as well as awarded contribution.
- `src/ringlens/scoring.py`: exposes configured thresholds/points for every rule;
  the existing rolling-window arithmetic, rates, priorities, and contributions persist.
- `src/ringlens/verify.py`: stable entity-ID edge endpoints/path normalization and
  Assessment node counts. Existing ingestion graph verification remains intact.
- `src/ringlens/schema.py`: additive Assessment label, history/latest/overlap
  relationship allowlist, and five range indexes, for 25 total. Initialization
  seeds no data, preserves old indexes/records, and remains idempotent.
- `src/ringlens/cli.py`: `investigate --create-case`, `--display-paths`,
  `cases list`, `cases show --case-id`; JSON/text output and explicit graph overrides.
  CLI delegates analysis, policy, and storage to reusable services.
- `tests/unit/test_case_contracts.py`: **18** offline identity/configuration,
  strict JSON/publication-model, input-bound, and incomplete/no-write checks.
- `tests/integration/test_cases.py`: **21** real-database case acceptance checks,
  including idempotency, alternate seeds, immutable snapshots, reviews/CLOSED
  status, overlapping groups, failures/crash/ack loss, MEDIUM/LOW policy,
  fresh-process readers, source-state guard, shared lease, and 20 → 25 schema upgrade.
- README, `docs/investigation.md`, `docs/cases.md`, `docs/graph-schema.md`, this
  status: workflow, contracts, commands, decisions/results, limitations, tool interfaces.
  `.dockerignore` additionally excludes the human case guide containing demo IDs.

### Identity, history, and failure decisions

Group key hashes dataset ID plus sorted candidate account IDs, excluding the seed.
Case ID is `inv_` + group key. Assessment ID hashes group key, successful import
revision, inclusive window, rule version, and scoring configuration (so v1 overrides
cannot collide). Evidence IDs use candidate order/refund query provenance rather
than seed-specific context; another seed yielding the same group/evidence reuses
case, assessment, and Evidence. Resource/display caps do not change complete
analytical identity. A different analytical payload under an existing key is rejected.

The immutable JSON snapshot retains the first request's seed, candidate depths,
complete 4/8-edge seed witnesses, semantic source paths, measurements/thresholds,
findings, mitigating facts, and source records. Physical graph IDs are retained
in read-only driver explanations but omitted from saved snapshots/identities.
Another seed can safely resume an interrupted write without replacing that snapshot.

Case creation requires COMPLETE MEDIUM/HIGH. New LOW groups return
NO_CASE_REQUIRED; exact existing cases can receive LOW reassessments without
automatic closure. Existing status and Review/ABOUT/HAS_REVIEW are preserved.
CLOSED + new assessment stays CLOSED and sets requires_review. Changed qualifying
groups create separate cases with canonical OVERLAPS links and review flags on
both; LOW changed groups create no case but flag overlapping existing cases and
return those IDs. Membership history is never overwritten or silently merged.

READY/revision is rechecked before staging, under the shared ingestion writer
lease, and before publication. New headers/assessments are LOADING while endpoints
and exact evidence/cardinalities are verified. A single final guarded query
publishes READY and consistent latest edge/property/current score without touching
reviewer status. Published cases keep the previous completed assessment readable
if an update fails. Readers exclude LOADING history and disclose pending IDs/counts.
Case HAS_EVIDENCE is the completed-history union; scores use each assessment's own
evidence, never the sum across history. Completed replays return EXISTING_UNCHANGED.

The multi-query write is not atomic as a whole. It uses idempotency/completion
markers instead of assuming a Neo4j transaction API. Process crashes can retain
LOADING and a lease up to 120 seconds. Retry safely finishes the same identity;
if imports changed, reassess under the new revision and leave stale unfinished
history explicitly pending. Final acknowledgement loss is reconciled from stored
completion state when possible; otherwise the action reports uncertainty, not a
claim that a write definitely failed. Unrecognized runtime errors are sanitized.

Case and ingestion writers share the existing renewable lease. Single-writer use
is required; fencing, transactional multi-query reads, concurrent review updates,
and arbitrary external Cypher mutation are not supported concurrency guarantees.
Legacy Investigation nodes without publication metadata are preserved, not silently
adopted as managed cases. Native uniqueness constraints remain uninstalled/unclaimed.

### Actual final checks

| Command/check | Actual result |
| --- | --- |
| `.venv/bin/pytest --run-persistence --tb=short -q` | **329 passed**, no skips, in **14.62 seconds** |
| Unit tests in final run | **263 passed**, including 18 new case-contract checks |
| Integration tests in final run | **66 passed**: 21 cases, 19 investigation, 22 ingestion, 4 existing graph/schema/persistence |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/ruff format --check .` | Passed; 43 Python files formatted |
| `UV_CACHE_DIR=.tools/uv-cache UV_PYTHON_INSTALL_DIR=.tools/python .tools/uv-package/bin/uv lock --check --offline` | Passed; existing 25-package lock unchanged |
| `ringlens investigate --help`, `ringlens cases --help` | Passed |
| `git diff --check` | Passed |

The first case-test run exposed an invalid overlap test attempt whose short window
excluded the seed; the test was replaced with a deterministic valid append fixture
that adds a connected account, preserving immutable originals. The final full run
above includes the corrected test. No implementation contract was relaxed.

Live checks verified actual supporting edge IDs/directions and complete bridge
witnesses; duplicate paths do not change scores. Boundary, disjoint burst, global
device-user, source validation, and controlled device-change tests from Phase 5
all still pass. Read-only investigation and identical completed case requests
leave the graph unchanged. Source verification still succeeds with saved cases.
Reviews and original completed snapshots survive append and lower-priority updates.
New connections and a genuinely fresh Python CLI process can read saved cases.
The original persistence check performs a real database restart and marker retention.
Write failures, crashes, acknowledgement loss, and readiness races are injected
test conditions; no application process/data was deliberately corrupted.

### Observed default results and commands

| Scenario | Candidate count after expansion 1 → 2 | Triggered rules | Score / priority | Case outcome |
| --- | --- | --- | --- | --- |
| Pattern A | 4 → 4 | R1–R4 | 90 / HIGH | CREATED; same/other seed EXISTING_UNCHANGED; append UPDATED |
| Pattern B | 3 → 5 | R1–R4 | 90 / HIGH | CREATED; same/other seed EXISTING_UNCHANGED; append UPDATED |
| Household base | 3 → 3 | R1, R5 | 35 / LOW | NO_CASE_REQUIRED for a new group |
| Household appended | 3 → 3 | R1–R5 | 100 / HIGH | CREATED; replay EXISTING_UNCHANGED |

Pattern B's two distant accounts have depth 2 and full bridge witnesses. The
base household has no synchronized burst; append supplies two disjoint qualifying
UTC dates and rates 3/5. A configurable 65/MEDIUM case was also created, then
updated to 0/LOW while remaining OPEN. Separate CLOSED/review preservation checks
passed. No synthetic result establishes real-world detection accuracy or wrongdoing.

```sh
docker compose up -d --wait
uv run ringlens schema init
uv run ringlens ingest --input data/demo/base
uv run ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15 --format json
uv run ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15 --create-case --format json
uv run ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15 --create-case --format json
uv run ringlens investigate --refund-id ref_00b5f41368d036aaeb49 --create-case --format json
uv run ringlens investigate --refund-id ref_9082163771ef0a5e5b26 --create-case --format json
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --create-case --format json
uv run ringlens cases list --format json
uv run ringlens cases show --case-id inv_08bd438528dd4ef3dadcc0940b424d9874784349e814c4527b9df85dd00577ee --format json
uv run ringlens ingest --input data/demo/new_evidence
uv run ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15 --create-case --format json
uv run ringlens investigate --refund-id ref_9082163771ef0a5e5b26 --create-case --format json
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --create-case --format json
uv run ringlens graph verify
```

Commands for fixture generation, fresh-graph use, historical windows, all stable
case IDs, and unit/integration checks are in `docs/cases.md`. Add `--graph NAME`
consistently for a separate demo. Existing configured cases return replay actions;
never clear an application graph/volume to force CREATED. `.venv/bin/ringlens`,
`.venv/bin/pytest`, and `.venv/bin/ruff` work when uv is not on PATH.

### Remaining scope and later agent interfaces

No required milestone behavior remains blocked/unverified. Real power-loss/backup
recovery, large-graph performance, concurrent snapshot/fenced writers, and review
submission/reopening policy were not implemented or claimed. Query-crash/ack tests
were simulated; saved-case application restart checks used actual fresh connections
and subprocesses. Evaluation ground truth was consumed only by evaluation tests;
production investigation/case services do not read labels or fixture files.

Later tools can call `investigator.investigate(store, refund_id, as_of=None,
config=None)`, `cases.apply_case_policy(store, result)`, `cases.list_cases(store,
limit=100)`, and `cases.get_case(store, case_id)`. The CLI composes these same
services. Use the internally computed result for persistence; do not accept
agent-invented scores/raw Cypher/evaluation input. The next milestone can add
review validation/lifecycle and the LLM orchestration layer without replacing
graph traversal, deterministic arithmetic, or case identity/history.

---

## Phase 5 — graph investigation, evidence, and transparent priority

Updated: 2026-10-16 (Asia/Kolkata).

Phase 5 is complete and verified with the pinned running FalkorDB server. Existing
Phase 1–4 worktree changes were preserved. No source/metadata schema, fixture bytes,
manifest/data contract, dependency declarations, or lockfile were replaced. No
applicable AGENTS.md was found. All database imports/mutations/cleanup for this
phase used owned, uniquely named disposable graphs; no application graph or named
volume was reset. Investigation itself performs only read-only graph operations.

### Files and implementation

- `src/ringlens/investigation_models.py`: strict configuration, windows,
  candidate depths, source/path provenance, rule signals, burst/rate facts,
  persistence-ready Evidence wrappers, structured errors, and completeness guards.
- `src/ringlens/investigation_queries.py`: fixed, parameterized refund-context,
  two-expansion discovery, ordered witness paths, order/refund/shipping evidence,
  and independent whole-graph device-user aggregation.
- `src/ringlens/investigator.py`: READY metadata validation/revision, explicit
  preconditions, bounded visited/frontier discovery, validated graph-returned
  source subset, deterministic evidence/path identities, scoring handoff,
  final metadata recheck, incomplete handling, and factual text rendering.
- `src/ringlens/scoring.py`: pure v1 rule arithmetic, distinct event counts,
  rolling disjoint burst extraction, refund-rate fractions, contributions,
  priority thresholds, and factual mitigating context. No I/O or discovery.
- `src/ringlens/cli.py`: `investigate --refund-id`, `--window-days`, `--as-of`,
  `--format text|json`, `--graph`, `--max-accounts`, and `--max-evidence`.
- `tests/unit/test_scoring.py`: 30 independent small-fixture rule/config/window
  tests; `tests/unit/test_investigator.py`: 13 offline input/failure/CLI checks.
- `tests/integration/test_investigation.py`: 19 real-database acceptance checks,
  including scenario evaluation, true path verification, caps/failures, revision
  changes, whole-graph counts, and a controlled device relationship comparison.
- `docs/investigation.md`, README: commands, exact rule/time semantics, actual
  human demo seeds/results, limits, failure semantics, and Phase 6 handoff.
  `.dockerignore` additionally excludes the human investigation guide, which
  contains scenario names/IDs; evaluation separation remains intact.

### Decisions and behavior

FalkorDB determines the seed context and every account/device expansion; Python
never discovers connections from a fixture or an alternate graph. Candidate
depths are zero/one/two; only newly discovered first-frontier accounts are expanded
again. One ordered actual witness path per discovered account explains reachability.
Shipping addresses are evidence, never traversal links. Graph-returned canonical
source records reuse Phase 4 hash/reference/cardinality checks; path multiplicity
does not multiply events. No cases, evidence nodes, reviews, LLM, or UI are created.

All timed observations use inclusive `[max(0, as_of - days*86400), as_of]` UTC
seconds. The default horizon is successful READY Dataset.as_of, not the clock.
Refund rates require both the refund and its associated order in the window;
burst analysis includes in-window refunds for older orders. Device users are
distinct accounts across the entire graph in the order window, independently
of candidate membership, so users beyond depth two can veto R1.

V1 contributes R1 +25, R2 +25, R3 +25, R4 +15, R5 +10, once per rule.
Priority thresholds are LOW 0–39, MEDIUM 40–69, HIGH 70–100. These are demo
prioritization rules, not probabilities or permanent account statuses.
Thresholds/points are explicit validated ScoringConfig values for Python callers
and are recorded in the result. CLI configures window and resource bounds.

Rolling bursts use inclusive 1800-second intervals, sorted `(requested_at,id)`.
The earliest qualifying interval consumes all its events before the next search.
Repeated synchronization requires disjoint refund IDs and distinct UTC start
dates (`start // 86400`); overlapping windows or several bursts on one date
cannot inflate repetitions. This deterministic greedy selection is deliberately
conservative, not a maximum-packing optimization.

Input revision hashes canonical successful dataset/import descriptors, excluding
runtime import timestamps, graph name, and database IDs. Evidence IDs hash rule,
version, source IDs, window, measured details, query/path IDs, and scoring config;
path IDs use semantic source properties and directed endpoints, excluding physical
IDs. Actual physical nodes/edges remain in returned explanation paths. Replaying
an identical batch leaves results unchanged. Historical assessment after append
has the same base analytical evidence with a new current input revision; this is
window filtering over available records, not bitemporal historical knowledge.

Default bounds are 100 candidates including the seed and 2000 distinct returned
paths (also the evidence/metadata query row bound). Sentinel extra rows expose
truncation. Caps, required query failures, inconsistent relationships/hashes, or
changed final metadata yield INCOMPLETE with partial evidence and no analysis,
score, or priority. CLI exits: 0 COMPLETE, 1 unavailable data, 2 invalid input,
3 INCOMPLETE. Precondition errors and a genuine LOW result remain distinct.
Metadata is checked before/after; a multi-query database snapshot is not claimed.

### Checks actually run

Previous checks were reverified before implementation: **202 unit tests passed**,
Ruff passed, and **25 existing integration tests passed** against real FalkorDB;
the restart-persistence test was initially skipped without its explicit option.

| Command | Actual final result |
| --- | --- |
| `.venv/bin/pytest --run-persistence --tb=short -q` | **290 passed**, no skipped tests, in 7.95 seconds |
| Unit checks in final run | **245 passed** (43 new offline investigation/scoring checks) |
| Integration checks in final run | **45 passed** (19 new investigation checks, 22 ingestion checks, 4 existing graph/schema/persistence checks) |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/ruff format --check .` | Passed |
| `UV_CACHE_DIR=.tools/uv-cache UV_PYTHON_INSTALL_DIR=.tools/python .tools/uv-package/bin/uv lock --check --offline` | Passed; resolved existing 25-package lock, no changes |
| `.venv/bin/ringlens investigate --help` | Passed; all requested options plus bounds displayed |
| `git diff --check` | Passed |

Database checks ran with approved sandbox network/socket access, not mocked
database responses. Injected failures/duplicate result paths are explicitly
test-controlled; scenario discovery/scoring/path checks used actual imported
FalkorDB nodes and edges. The existing persistence test performed a real orderly
restart, verified the retained marker, and cleaned up only its graph/marker.

### Observed results and exact commands

With seed 42 / base horizon 2026-10-15T10:00:00Z / default 30-day window:

| Human scenario | First → second candidate count | Burst dates | Per-member rate | Score / priority |
| --- | --- | --- | --- | --- |
| Pattern A | 4 → 4 | 3 | 3/5 | 90 / HIGH (R1–R4) |
| Pattern B | 3 → 5 | 3 | 3/5 | 90 / HIGH (R1–R4) |
| Household base | 3 → 3 | 0 | 1/5 | 35 / LOW (R1, R5) |
| Household after append | 3 → 3 | 2 | 3/5 | 100 / HIGH (R1–R5) |
| Ordinary control | 1 → 1 | 0 | 1/5 | 0 / LOW |

Pattern B has depth counts 1 / 2 / 2. Its two distant accounts require the second
expansion. Duplication of returned paths and READY reingestion do not change
the assessment. Device counts include a separate fixture's three users outside
the discovered candidates. Inclusive lower/upper boundaries are live-tested,
including seeds exactly on both endpoints; out-of-window order/device uses and
refunds do not contribute. Caps and simulated query deadlines retain partial
paths while withholding final scores. Missing seeds/non-READY data produce
explicit failures. Altering the seed's device edges in a separate valid fixture,
with identical refund records and order amounts/times, changes pattern A from
4 candidates / 90 to 1 candidate / 0, demonstrating graph-controlled discovery.

```sh
docker compose up -d --wait
uv run ringlens schema init
uv run ringlens ingest --input data/demo/base
uv run ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15
uv run ringlens investigate --refund-id ref_9082163771ef0a5e5b26 --format json
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --as-of 2026-10-15T10:00:00Z
uv run ringlens ingest --input data/demo/new_evidence
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --format json
uv run pytest tests/unit/test_scoring.py tests/unit/test_investigator.py
uv run pytest tests/integration/test_investigation.py --run-integration
uv run pytest --run-persistence
uv run ruff check .
uv run ruff format --check .
```

Use `.venv/bin/ringlens`, `.venv/bin/pytest`, and `.venv/bin/ruff` if uv is not
on PATH. Add `--graph NAME` consistently for isolated manual graphs. Detailed
generation/startup and custom bounds/config commands are in `docs/investigation.md`.

### Limits and Phase 6 handoff

No required Phase 5 check remains blocked or unverified. Large-graph performance,
concurrent snapshot isolation, predictive calibration, and power-loss recovery
are not claimed. All query failures/import-state changes were injected in owned
test graphs; no application data was altered. The two-expansion search is complete
for its stated depth, not an exhaustive arbitrary connected-component search.

Phase 6 should persist only COMPLETE results for the expected READY revision.
Use existing case/evidence/review models/allowed edges; define case identity and
lifecycle, preserve earlier assessments and reviewer history, MERGE stable
evidence without conflicting overwrites, and retain semantic provenance/config/
input revision in validated JSON strings (or a reviewed versioned extension).
Validate HAS_REVIEW/ABOUT membership and outcomes. A decision never establishes
an account's permanent status. Stop point: Phase 5; no case writer/review memory,
orchestration, LLM, or dashboard has been added.

---

## Phase 4 — validated, idempotent FalkorDB ingestion

Updated: 2026-10-16 (Asia/Kolkata).

Phase 4 is complete and verified against the running pinned FalkorDB container.
The existing worktree, canonical fixture bytes/manifests, strict source/metadata
models, graph vocabulary, and dependency lockfile were preserved. No applicable
`AGENTS.md` was found. No application graph or named volume was cleared or deleted.

### Files and behavior

- `src/ringlens/ingest.py`: complete pre-write validation, base/append planning
  from existing FalkorDB state, batched immutable-ID MERGE writes, endpoint-count
  checks, verification before publication, import state/failure tracking, and
  safe original-batch retry. READY replays return verified no-ops.
- `src/ringlens/import_queries.py`: fixed labels/types and parameterized source,
  relationship, import-state, and actual-path queries. Default chunks contain 50
  records. Source hashes/properties are assigned only ON CREATE.
- `src/ringlens/verify.py`: reconstructs records from actual graph properties and
  edge references; verifies source hashes, canonical manifest file digests/counts,
  uniqueness, cardinality, ownership, chronology, readiness, and cutoff. Structured
  reports retain raw node counts and distinct-ID counts. Stats includes case node
  and relationship counts. Smoke queries return genuine FalkorDB Path objects;
  CLI serializes actual node/edge IDs, properties, and directions.
- `src/ringlens/graph_store.py`: a fail-fast, renewable 120-second import lease
  in the same FalkorDB service. It is transient coordination, never durable import
  truth. Dry-runs and known READY no-ops do not acquire it.
- `src/ringlens/cli.py`: `ingest --input ... [--dry-run] [--graph ...]`, `graph
  verify`, `graph stats`, and `graph paths`; graph commands and schema init accept
  explicit graph overrides. Verification failures, including connection failures,
  return JSON reports and nonzero exits.
- `tests/integration/test_ingest.py`: 22 real-database acceptance checks using
  uniquely named disposable graphs; `tests/unit/test_ingest_validation.py`: ten
  offline preflight, dry-run, manifest-integrity, graph-override, and CLI checks.
- `README.md` and `docs/ingestion.md`: exact commands, storage/provenance,
  validation, failure/retry, readiness, paths, and the single-writer boundary.

### State and provenance decisions

Strict Dataset/ImportBatch node property contracts are unchanged. The importer
stores `manifest_json` (canonical JSON string) and `source_ids` (distinct string
array) on each HAS_BATCH relationship. These are importer provenance, enabling
verification against applied manifests without caching full source records or
reading hidden scenario labels. Foreign keys remain graph edges; source_hash
includes the complete canonical application row, including its foreign keys.

Input validation precedes any metadata/source writes. Dry-run creates no graph,
nodes, relationships, indexes, or lease keys, including on an absent graph.
Existing source hash/properties/references must match immutable content. New
appends resolve references against actual graph data plus incoming records, reject
duplicate refunds across that union, and must advance the last successful cutoff.
An identical completed base replay after append stays a no-op at the latest cutoff.

Before source writes Dataset is INGESTING and the current batch LOADING. Other
batch history is preserved. Full graph verification gates one final READY publish
query, which advances Dataset.as_of only upon success. Handled failures attempt
FAILED with a sanitized error; disconnects/process crashes can leave LOADING.
Append failure retains the previous successful as_of. Readers must require READY.
Retries complete the same batch safely; the original started_at is retained.
Lost READY acknowledgements cannot downgrade a completed batch to FAILED.

The import spans multiple queries and is not atomic as a whole. One writer per
graph is required. Different unfinished batches block new imports even after lease
expiry; retry the original batch first. The lease guards normal competing writers
but is not fencing or general concurrent-ingestion support. No native uniqueness
constraint is installed or claimed. Actual process-kill recovery may require
waiting for the lease (up to 120 seconds); simulated crash/retry is tested.

### Actual verification results

Docker is now installed. An initial sandboxed socket/network check was blocked;
the first automatic approval review timed out. Its permitted retry succeeded:
`docker compose ps --format json` reported the pinned container healthy and doctor
reported configuration valid, database reachable, and FalkorDB available. Database
tests then ran with approved sandbox escalation, not mocked server responses.

| Check | Actual result |
| --- | --- |
| `.venv/bin/pytest --run-persistence --tb=short` | **228 passed**, no skipped tests, in 4.30 seconds |
| Phase 4 ingestion integration tests in that run | **22 passed** against real FalkorDB |
| Offline unit tests in that run | **202 passed** |
| Existing graph/schema/persistence integration tests | **4 passed**, including actual restart and marker retention/cleanup |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/ruff format --check .` | Passed |
| `uv lock --check --offline` with existing local uv/cache paths | Passed; no dependency changes |
| `git diff --check` | Passed |
| `ringlens ingest --help`, `ringlens graph --help` | Passed |

The first integration run exposed missing null metadata fields (FalkorDB omits
them as graph properties); reader reconstruction was fixed before rerunning.
A full-suite collection collision between unit/integration module filenames was
resolved by naming the unit module `test_ingest_validation.py`. Both fixes are
included in the final successful full run above.

On owned disposable graphs, the tests verified:

- Base READY: 50 accounts, 42 devices, 46 addresses, 250 orders, 42 refunds,
  834 canonical source relationships, and one batch. Replay changes nothing.
- Append READY: unchanged account/device/address/order counts, 48 refunds,
  846 canonical source relationships, and two batches. Replaying append or base
  leaves the same graph and metadata intact.
- Base cutoff `2026-10-15T10:00:00Z`; successful append cutoff
  `2026-10-17T11:00:00Z`. Failure does not advance the successful cutoff.
- Invalid checksums, missing account/device/address/order references, conflicting
  immutable IDs, second refunds, foreign dataset assignment, and non-advancing
  appends fail without graph writes.
- Failures during node/edge writes leave FAILED; simulated process crashes leave
  LOADING. Same-input retries complete without duplication. An actual held lease
  rejects another writer. Missing endpoints and even a simulated false successful
  chunk count cannot publish an incomplete graph READY.
- Minimal valid Investigation/Evidence/Review nodes and their four relationships
  preserve their properties and database IDs across append.
- Graph-returned refund/device/account/address paths and bounded account/device/
  peer paths are real client Path objects. Peer counts deduplicate IDs while
  multiple explanation paths remain available.
- Verification detects duplicate nodes/edges, source content altered even with a
  recomputed hash, and inconsistent Dataset.as_of. Ingestion never reads evaluation.

No required Phase 4 behavior remains blocked. The default application graph was
not loaded as part of testing: all source import/mutation/cleanup tests targeted
their owned disposable graphs. The shared database was briefly restarted by the
existing persistence test, without deleting the named volume. Power-loss/backup
recovery and genuinely concurrent/fenced ingestion remain outside this phase.

### Exact handoff commands

```sh
# 1. Start FalkorDB (configure .env first).
uv sync --locked
docker compose up -d --wait
uv run ringlens doctor

# 2. Initialize the configured graph schema.
uv run ringlens schema init

# 3. Reproduce the existing fixtures; identical files are preserved.
uv run ringlens data generate --seed 42 --as-of 2026-10-15T10:00:00Z --output data/demo --evaluation-output evaluation/demo_ground_truth.json

# 4. Validate against existing graph state, then import the base.
uv run ringlens ingest --input data/demo/base --dry-run
uv run ringlens ingest --input data/demo/base

# 5. Verify and inspect actual graph paths.
uv run ringlens graph verify
uv run ringlens graph stats
uv run ringlens graph paths --limit 5

# 6. Append and verify the new evidence; repeat is a no-op.
uv run ringlens ingest --input data/demo/new_evidence --dry-run
uv run ringlens ingest --input data/demo/new_evidence
uv run ringlens graph verify

# 7. Unit, integration, and existing restart-persistence checks.
uv run pytest tests/unit
uv run pytest tests/integration/test_ingest.py --run-integration
uv run pytest --run-persistence
uv run ruff check .
uv run ruff format --check .
```

Add `--graph NAME` consistently for isolated manual graphs. The local
`.venv/bin/ringlens`, `.venv/bin/pytest`, and `.venv/bin/ruff` equivalents work when
uv is not on PATH. `docker compose down` stops containers and preserves data.
Stop point: Phase 4. Graph investigation/scoring is the next phase; it is not
implemented here. No case automation, LLM, or UI was added.

---

## Phase 3 — deterministic synthetic fixtures

Updated: 2026-10-16 (Asia/Kolkata).

Implemented fixture generation and file/scenario validation on the existing Phase 2
models and byte contract. The worktree already contained the Phase 2 implementation;
that work was preserved. No applicable `AGENTS.md` was found. The source models,
graph schema, data contract, dependency declarations, and lockfile were not changed.

### Files and implementation decisions

- `src/ringlens/synthetic.py`: a local seeded generator, explicit whole-second UTC
  cutoff parsing, deterministic opaque IDs, shuffled memberships before account ID
  assignment, and separate evaluation output. No clock, random UUIDs, global random
  state, graph connection, or LLM is used.
- `src/ringlens/dataset_io.py`: canonical six-file batches, exact manifest/content
  hashes, safe file reading, duplicate-key/ID and canonical-byte validation, and
  cross-record validation. Append validation resolves references against a supplied
  base, checks immutable-ID conflicts and duplicate refunds across the union, and
  rejects backward horizons. Identical immutable reference replays are allowed by
  the existing contract. No ingestion is implemented.
- `src/ringlens/cli.py`: `data generate` with explicit as_of and optional overwrite;
  `data validate --input ...` with `--base ...` for appends. These commands do not
  load database settings or construct GraphStore. Errors exit nonzero.
- `tests/unit/test_synthetic.py`: independent JSON/topology/time checks for all
  groups, both expansions, counts/rates/bursts, append behavior, alternate seeds
  and cutoffs, deterministic bytes across interpreter hash seeds, overwrite refusal,
  corrupted canonical files/manifests, and evaluation separation.
- `data/demo/base/`: all five JSONL files and manifest. `data/demo/new_evidence/`:
  six refund records, four empty JSONL files, and append manifest, preserving the
  exact five-file contract.
- `evaluation/demo_ground_truth.json`: evaluation-only memberships, seed refunds,
  expansions, rates, and burst expectations; no operational manifest refers to it.
- `docs/demo-scenarios.md` and `README.md`: reproduction/validation instructions
  and the human walkthrough. `.dockerignore` also excludes the human guide and
  this status document because they contain scenario labels and starting IDs;
  existing evaluation exclusions stay.

Fixed seed/as_of produces byte-identical runtime and evaluation artifacts. Rows
sort by opaque ID; keys/UTF-8/LF and hash preimages follow Phase 2 exactly. Generation
checks every output for conflicts before writing; identical reruns leave existing
files alone, while differing files require `--overwrite`. Extra unmanaged files
are never deleted. Evaluation output must be outside the operational output root
and beneath an `evaluation/` directory covered by Docker exclusions.

### Actual fixture counts and demo seeds

Base horizon is `2026-10-15T10:00:00Z`; append horizon is `2026-10-17T11:00:00Z`.

| Kind | Base | Append | Combined |
| --- | ---: | ---: | ---: |
| accounts | 50 | 0 | 50 |
| devices | 42 | 0 | 42 |
| addresses | 46 | 0 | 46 |
| orders | 250 | 0 | 250 |
| refunds | 42 | 6 | 48 |

Each account has five orders in both 30-day horizons. Ordinary refunds: 12;
pattern A: 12; pattern B: 15; household: 3 base plus 6 append. Two ordinary address
pairs supply noise. Device sharing is exclusive to the specified groups. Base
pattern A/B each have three dates of synchronized refunds, while ordinary and
household base records have no three-account burst. Append creates two household
bursts and changes each household member's refunded-order fraction from 1/5 to 3/5.

| Scenario | Seed refund (seed 42) | Accounts after expansion 1 → 2 |
| --- | --- | --- |
| Ordinary control | `ref_ef82ea0a22e9291d2977` | 1 → 1 |
| Pattern A | `ref_2b42ec18e21a70bdfa15` | 4 → 4 |
| Pattern B | `ref_9082163771ef0a5e5b26` | 3 → 5 |
| Household | `ref_4d44e5cc3e0a8dae77fd` | 3 → 3 |

Base batch ID: `batch_6fee3da918430b0355677a79019a0cfb05b1198a77cf54b209905e3d754d3f31`.
Append batch ID: `batch_d08113da25d2c0f126601ecbc6d69bc515e1e7300cb8d1665a985eec6be83c98`.

### Commands and verification

```sh
uv run ringlens data generate --seed 42 --as-of 2026-10-15T10:00:00Z --output data/demo --evaluation-output evaluation/demo_ground_truth.json
uv run ringlens data validate --input data/demo/base
uv run ringlens data validate --input data/demo/new_evidence --base data/demo/base
uv run pytest tests/unit/test_synthetic.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The local `.venv/bin/ringlens`, `.venv/bin/pytest`, and `.venv/bin/ruff` equivalents
were used. Initial generation/validation was also exercised under `/private/tmp`.
One explicit `--overwrite` rerun refreshed generator-owned evaluation metadata
during development; the default regeneration path was tested separately.

| Check | Outcome |
| --- | --- |
| Default generation command | Wrote the requested base, append, and separate evaluation files |
| Base file validation | Passed; actual counts match the table above |
| Append validation with `--base` | Passed; only six additional refunds |
| Append validation without context | Expected nonzero exit with actionable `--base` message |
| Phase 3 fixture tests | 36 passed |
| Full default pytest suite | 192 passed; 4 database integration tests skipped by default |
| Ruff lint / formatting | Passed |
| Lockfile / whitespace checks | Passed; no dependency changes |

Scenario checks parse JSON independently from the generator's models and reconstruct
device membership through orders. They verify the chosen B seed is on D1, that the
bridge's in-window D2 use is necessary to reach two more accounts, and that each
planted burst has exactly one event per member within 30 minutes. Alternate seeds
0, 1, 42, 43, 991, and 2^63−1 retain guarantees. Alternate cutoffs include the last
second of a UTC date. Reproducibility checks cover every file and manifest plus
evaluation bytes; subprocess tests vary `PYTHONHASHSEED`.

There are no Phase 3 database or network blockers: generation/validation run
entirely offline. Database integration tests were not rerun for this file-only
phase; Phase 2's successful user-reported live checks remain recorded below.
No application graph, named volume, investigation, or review was changed. Next
work is ingestion under the existing conflict/readiness contract; scoring, LLM,
agent tools, and dashboard remain deferred. Stop point: Phase 3.

---

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
