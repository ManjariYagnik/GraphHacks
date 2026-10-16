# Read-only graph investigation (Phase 5)

RingLens follows actual FalkorDB relationships from a refund, performs two account
expansions through devices, and returns source records, explanation paths, and
explicit v1 priority contributions. It creates no Investigation, Evidence, or
Review nodes. There is no LLM, case automation, or dashboard in this phase.

This guide contains human demo scenario names and IDs. It is excluded from runtime
Docker build contexts with `evaluation/` and the other human scenario/status guides.
Production investigation code reads neither fixture files nor ground truth.

## Run the demonstration

Configure `.env` as described in the README, then run from the repository root:

```sh
uv sync --locked
docker compose up -d --wait
uv run ringlens doctor
uv run ringlens schema init
uv run ringlens data generate --seed 42 --as-of 2026-10-15T10:00:00Z --output data/demo --evaluation-output evaluation/demo_ground_truth.json
uv run ringlens ingest --input data/demo/base

# Pattern A and pattern B, using real seed-42 fixture IDs.
uv run ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15
uv run ringlens investigate --refund-id ref_9082163771ef0a5e5b26 --format json

# Explicit base horizon also works if append was imported previously.
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --as-of 2026-10-15T10:00:00Z

uv run ringlens ingest --input data/demo/new_evidence
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --format json
```

If uv is not on PATH, use `.venv/bin/ringlens` or
`.tools/uv-package/bin/uv`. Every investigation accepts `--graph NAME`, as do
schema initialization and ingestion. Use the same graph option throughout an
isolated manual demonstration. Never clear an application graph or volume to
reset the demonstration; a historical `--as-of` filters the available records.

The default format is `text`; `--format json` returns the full structured result.
`--window-days` defaults to 30, `--max-accounts` to 100 (including the seed), and
`--max-evidence` to 2000. The latter bounds distinct returned path objects and the
rows of each metadata/evidence query; a sentinel extra row detects truncation.
Source records come only from those paths and are also bounded by their endpoints.
There are at most five triggered rule evidence objects. The separate fixed
context limit detects ambiguity; ordered discovery-path queries intentionally
choose one representative witness per newly reached account, not all witnesses.

## Verified scenario results

These are observations from real FalkorDB tests on owned disposable graphs, with
the default rules, 30-day window, and fixtures above:

| Scenario / seed refund | Candidates after first → second expansion | Refund bursts | Rate per member | Rules | Score / priority |
| --- | --- | --- | --- | --- | --- |
| Pattern A / `ref_2b42ec18e21a70bdfa15` | 4 → 4 | 3 on separate dates | 3/5 | R1, R2, R3, R4 | 90 / HIGH |
| Pattern B / `ref_9082163771ef0a5e5b26` | 3 → 5 | 3 on separate dates | 3/5 | R1, R2, R3, R4 | 90 / HIGH |
| Household, base / `ref_4d44e5cc3e0a8dae77fd` | 3 → 3 | 0 | 1/5 | R1, R5 | 35 / LOW |
| Household, appended / same seed | 3 → 3 | 2 on separate dates | 3/5 | R1–R5 | 100 / HIGH |
| Ordinary control / `ref_ef82ea0a22e9291d2977` | 1 → 1 | 0 | 1/5 | None | 0 / LOW |

Pattern B has depth counts 1 seed, 2 first-expansion peers, and 2 second-expansion
peers. Its bridge is reached at depth 1; its second device reaches the two distant
accounts at depth 2. Pattern A's device has four users; B's devices have three
each. The first planted bursts span 1200 seconds (20 minutes). The household has
shared infrastructure in the base but no synchronized burst or elevated rate.
Append adds six refunds and advances the horizon; its two bursts also span
20 minutes. No account is declared permanently abusive or benign.

## Time, readiness, and input revision

All filters use **inclusive** integer UTC seconds:
`[max(0, as_of - window_days * 86400), as_of]`. Order placement determines timed
device use and shipping. Refund request time determines inclusion in burst
analysis. A seed exactly on either boundary is allowed; events outside are excluded.

Default `as_of` is the successfully imported READY Dataset's `as_of`, never the
current clock. The base horizon is `2026-10-15T10:00:00Z`; appended horizon is
`2026-10-17T11:00:00Z`. Explicit CLI timestamps must have whole seconds and a UTC
`Z` or `+00:00` offset. Later-than-available assessments and out-of-window seeds
are invalid. Missing seeds or non-READY datasets are unavailable data.

`input_revision` is SHA-256 of canonical UTF-8 JSON containing dataset ID, schema
version, successful imported horizon, and successful batches sorted by ID. Each
batch entry contains its ID, content hash, mode, and manifest horizon. Runtime
import timestamps, graph name, wall-clock time, and physical database IDs do not
enter the revision. All batches must be READY, and Dataset.as_of must agree with
the successful import history.

Historical assessments filter the records currently available in the graph; they
are not a bitemporal reconstruction of what was known before a later import.
Their revision still identifies the complete currently imported input. Reassessing
the base household horizon after append produces the same analytical evidence
and priority, with the updated input revision.

Metadata is rechecked after gathering/scoring and before a final result is
published. A changed revision or non-READY state makes the result INCOMPLETE.
This check is not a multi-query snapshot or concurrency guarantee. Use the existing
single-writer import workflow and investigate stable READY data.

## Queries, discovery, and evidence

`investigation_queries.py` owns the fixed label/type allowlist queries. Values
are parameters, and `GraphStore.read()` uses GRAPH.RO_QUERY with the existing
server query deadline. The pinned FalkorDB v4.20.7 was exercised with these
queries. See FalkorDB's [OPTIONAL MATCH](https://docs.falkordb.com/cypher/optional-match)
and [ORDER BY](https://docs.falkordb.com/cypher/order-by) documentation.

1. `refund_context.v1` retrieves the seed plus actual requester, refund/order,
   owner, device, and address paths. Missing/ambiguous required paths or an owner
   mismatch are errors. The existing source-property/hash and cross-record
   validators also check this graph-returned subset.
2. `expansion_1.v1` and `expansion_2.v1` return distinct account IDs from
   `Account → Order → Device ← Order ← Account`. Both orders must be in the
   window. Only the newly discovered first frontier is expanded again. Visited
   IDs are excluded in FalkorDB; Python tracks depth and deduplicates candidates.
   Addresses are never traversal edges. Each new account retains `reached_from`
   and a deterministic, actual four-edge path (`expansion_N_path.v1`), so depth-2
   explanations can be followed back through the depth-1 account to the seed.
3. `orders.v1` and `refunds.v1` retrieve all required source paths for candidates
   within the window, including shipping and refund ownership. Refund-associated
   orders are retained as context even if their placement predates the window.
   Distinct order/refund/account IDs determine counts. Repeated paths do not
   multiply events. Missing required references, inconsistent hashes, or duplicate
   canonical relationships make the assessment incomplete.
4. `device_users.v1` independently counts distinct in-window account users of
   every candidate-used device **across the whole graph**. There is deliberately
   no candidate-account predicate. Users beyond both discovery expansions can
   veto the device's R1 eligibility. The result and aggregate query ID accompany
   the candidate device-use paths.

The temporary source collection is only the returned evidence subset. It is not
an alternate graph store or a Python implementation of relationship discovery.
No NetworkX traversal or runtime source-file loading is used.

Every path includes its query ID, observation window, source IDs, physical node
and edge IDs, properties, and directions. Complete results also retain validated
canonical source records, including source foreign keys reconstructed from edges.
Events outside the window can appear as seed/refund context, but cannot contribute
to windowed device, shipping, or refund-rate order counts.

## Transparent rules (v1 defaults)

| Rule | Trigger | Contribution |
| --- | --- | ---: |
| R1 | ≥3 candidate users of one device, with ≤5 distinct users of that device across the whole graph in the window | 25 |
| R2 | ≥3 distinct candidate accounts request refunds within one rolling inclusive 1800-second interval | 25 |
| R3 | ≥2 disjoint qualifying bursts with distinct UTC start dates | 25 |
| R4 | ≥3 candidates each have ≥5 in-window orders and refunds for ≥50% of those distinct orders | 15 |
| R5 | ≥3 candidates ship in-window orders to one address | 10 |

Each rule contributes once, even if several devices, addresses, or bursts qualify.
Priorities are LOW 0–39, MEDIUM 40–69, and HIGH 70–100. These demonstration rules
are not a calibrated probability of fraud.

Refund-rate numerators require **both** refund request and associated order
placement in the assessment window. The denominator contains all distinct orders
placed by that account in the window. Refunds for older orders can contribute to
R2/R3 but never to this numerator. Fractions are compared with integer arithmetic;
duplicate paths cannot inflate a numerator or denominator.

Burst extraction sorts distinct refunds by `(requested_at, id)`. Starting at the
earliest event, it considers all events through `start + 1800`, inclusively. If
three distinct accounts qualify, it records that interval and consumes **all** its
events before continuing. Otherwise it advances the left endpoint by one event.
This is a deterministic, conservative greedy selection, not a maximum-packing
optimization. It uses rolling intervals, not clock buckets. Selected bursts share
no refund IDs; several disjoint bursts on one UTC start date still count as only
one date for R3. A burst across midnight belongs to its first event's UTC day
(`start // 86400`). The reported burst end is its last actual event.

`ScoringConfig` exposes thresholds and each rule's points to Python callers.
`InvestigationConfig(scoring=ScoringConfig(...))` passes them to `investigate()`;
the result records the full configuration. Integer/boolean coercion is forbidden,
unknown fields are rejected, minimum accounts is at least three, rate thresholds
cannot exceed one, and total configured points cannot exceed 100. Version remains
v1; a semantic rule/algorithm change should introduce a new version. CLI options
configure the window and resource bounds; rule overrides are an explicit Python
API choice, with no new file format or environment settings.

## Structured results and failure semantics

`InvestigationResult` includes seed IDs, assessment/window, dataset ID and revision,
successful batch IDs, full config, completeness, candidate depths, source records,
paths, independent device counts, all five evaluated rules, burst/rate facts,
triggered evidence, total/priority, mitigating context, limitations, and errors.
Mitigating facts include absent synchronization, low refund rates, and devices
excluded because they have more than five users. Shared infrastructure alone
can have ordinary explanations; no reviewer decision is invented.

Triggered findings wrap the existing strict `Evidence` model, ready for a future
case writer, with canonical validated `details_json`, query IDs, and path IDs.
Evidence IDs are `ev_` plus SHA-256 of canonical rule ID/version, source IDs,
window, measured details, supporting path IDs/query IDs, and scoring configuration.
Path IDs similarly hash semantic source properties/directed endpoints, query ID,
and window. Physical database IDs remain in returned paths but are excluded from
these identities. Identical data/configuration yields the same analytical result
and evidence IDs; an identical READY import is a no-op and changes nothing.

| Exit | Meaning |
| --- | --- |
| 0 | COMPLETE, including a genuine LOW result |
| 1 | Data unavailable: absent graph/refund or non-READY import; connection setup failure |
| 2 | Invalid arguments/configuration, future horizon, or out-of-window seed |
| 3 | INCOMPLETE: cap, required query failure, inconsistent evidence, or changed import state |

JSON precondition failures return `error.kind` and `error.message`. Incomplete
results preserve the seed/context/candidates/paths obtained so far, query-tagged
errors, and limitations. Their `analysis`, `total_score`, and `priority` are null;
there is no normal LOW result after missing work. Validated source records retain
the last complete evidence subset if a later query/path bound fails.

## Checks and observed limits

```sh
uv run pytest tests/unit/test_scoring.py tests/unit/test_investigator.py
uv run pytest tests/integration/test_investigation.py --run-integration
uv run pytest --run-persistence
uv run ruff check .
uv run ruff format --check .
```

Actual Phase 5 regression result: **290 passed** (245 unit, 45 integration,
including 19 investigation integration checks and the existing real database
restart-persistence test). Ruff and offline lockfile consistency checks passed.
Tests use uniquely named graphs, verify absence before creation, and clean up
only their owned graph. The persistence check briefly restarts the shared local
service and preserves its named volume. No application graph was reset/imported
as part of these tests.

Checks cover both expansions, all five rule contributions, exact inclusive
boundaries, events outside the window, refund-rate denominators, overlapping and
cross-midnight bursts, whole-graph device counts beyond candidates, idempotent
ingestion, deterministic results/evidence, actual physical edge verification,
no local/evaluation reads or investigation writes, missing seeds/non-READY data,
caps, simulated query deadlines, and a metadata-state change during assessment.
The controlled comparison changes the seed account's device edges in a separate
valid imported fixture while preserving refund records, order amounts, and times:
pattern A changes from 4 candidates / 90 to 1 candidate / 0. This confirms that
the runtime graph determines discovery and supporting evidence.

No required Phase 5 behavior remains unverified. Concurrent snapshot isolation,
maximum burst-packing, large-graph performance, and calibrated predictive accuracy
are not claimed. Two expansions intentionally do not exhaust an arbitrarily
large connected component. Query failures/state changes were injected in tests;
no production process was deliberately disconnected or corrupted.

## Phase 6 case and reviewer persistence

Persist only COMPLETE results against the expected READY input revision, using
the existing Investigation, Evidence, and Review models and allowed relationships.
Assign case identity/lifecycle and runtime created/updated timestamps, link distinct
candidate accounts with INCLUDES, and MERGE deterministic Evidence IDs without
overwriting differing content. Preserve semantic paths, source IDs, window, rule
configuration, revision, and measured facts in validated JSON strings (or a reviewed
versioned contract extension); never assign nested dictionaries to graph properties.
Physical edge IDs alone are insufficient after graph recreation.

Phase 6 must implement review outcomes/explanations/timestamps, HAS_REVIEW and
ABOUT endpoint validation, evidence membership, case transition/reopen policy,
and preservation of earlier assessments/reviews when append changes the input.
An account gains no permanent status from a priority or reviewer decision. No such
case writer, review workflow, or persistence is implemented in Phase 5.
