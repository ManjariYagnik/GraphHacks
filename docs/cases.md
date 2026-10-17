# Cases and immutable assessment history

The case milestone extends the existing graph investigation/scoring services.
FalkorDB remains the runtime authority. Read-only investigation is unchanged unless
`--create-case` is supplied. The next milestone adds the [human review workflow](reviews.md), [tool-calling
agent](agent.md), and policy-controlled reopening; the dashboard remains deferred. Source records/manifests and ingestion
contracts remain unchanged. This human guide contains demo IDs and is excluded from runtime Docker
contexts alongside `evaluation/` and the other human scenario guides.

## Run the complete workflow

Configure `.env` using the README, then run from the repository root. These IDs
belong to seed 42 and base horizon `2026-10-15T10:00:00Z`:

```sh
uv sync --locked
docker compose up -d --wait
uv run ringlens doctor
uv run ringlens schema init
uv run ringlens schema init
uv run ringlens data generate --seed 42 --as-of 2026-10-15T10:00:00Z --output data/demo --evaluation-output evaluation/demo_ground_truth.json
uv run ringlens ingest --input data/demo/base

# Read-only assessment: creates nothing.
uv run ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15 --format json

# Pattern A: CREATED, then EXISTING_UNCHANGED.
uv run ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15 --create-case --format json
uv run ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15 --create-case --format json

# Another A seed: same case and assessment, EXISTING_UNCHANGED.
uv run ringlens investigate --refund-id ref_00b5f41368d036aaeb49 --create-case --format json

# Pattern B: reaches distant accounts through expansion two, then creates a case.
uv run ringlens investigate --refund-id ref_9082163771ef0a5e5b26 --create-case --format json

# Household: 35 / LOW, NO_CASE_REQUIRED on a new graph.
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --create-case --format json

uv run ringlens cases list --format json
uv run ringlens cases show --case-id inv_08bd438528dd4ef3dadcc0940b424d9874784349e814c4527b9df85dd00577ee --format json

# New input revision and horizon. A/B append history rather than replacing it.
uv run ringlens ingest --input data/demo/new_evidence
uv run ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15 --create-case --format json
uv run ringlens investigate --refund-id ref_9082163771ef0a5e5b26 --create-case --format json

# Household: now 100 / HIGH, CREATED; repeating it is EXISTING_UNCHANGED.
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --create-case --format json
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --create-case --format json
uv run ringlens cases list
uv run ringlens graph verify
```

If uv is not on PATH, substitute `.venv/bin/ringlens` or the local uv binary
`.tools/uv-package/bin/uv`. All investigation, ingestion, schema, and case commands
accept `--graph NAME`; pass the same explicit name throughout for a separate manual
demo. Never delete an application graph or named volume to reset the workflow.
If the configured graph already has append/cases, repeats return existing results.
Use a fresh graph name for the exact CREATED/NO_CASE_REQUIRED sequence above.

Default `as_of` is READY Dataset.as_of. Use `--as-of 2026-10-15T10:00:00Z` for
a historical base-window assessment after append; it still has the current input
revision. On an existing household case this LOW reassessment is attached, rather
than treated as a new group or automatically closing the case. Historical filtering
does not recreate past knowledge before an import.

### Observed default-fixture outcomes

| Human scenario | Seed refund | Expansion 1 → 2 | Rules / score | Case actions |
| --- | --- | --- | --- | --- |
| Pattern A | `ref_2b42ec18e21a70bdfa15` | 4 → 4 | R1–R4 / 90 HIGH | CREATED; repeat/other seed EXISTING_UNCHANGED; append UPDATED |
| Pattern B | `ref_9082163771ef0a5e5b26` | 3 → 5 | R1–R4 / 90 HIGH | CREATED; repeat/other seed EXISTING_UNCHANGED; append UPDATED |
| Household base | `ref_4d44e5cc3e0a8dae77fd` | 3 → 3 | R1, R5 / 35 LOW | NO_CASE_REQUIRED for a new group |
| Household appended | same seed | 3 → 3 | R1–R5 / 100 HIGH | CREATED; repeat EXISTING_UNCHANGED |

The deterministic seed-42 case IDs are:

- A: `inv_08bd438528dd4ef3dadcc0940b424d9874784349e814c4527b9df85dd00577ee`
- B: `inv_424afacab8e9c9bbf84f504eef395356c83203d5db6d22d30550676c2e1aa670`
- Household: `inv_b3a91ceaf1c249cbc0ea0da5def7897f46f58b6c166c1de4aee092c9bdf6e954`

The tests also created a configurable 65 / MEDIUM case and updated it to 0 / LOW
without changing OPEN status. A CLOSED case retained its status and review for
unchanged evidence or a lower-priority reassessment. Material new qualifying evidence can now reopen it. These are prioritization heuristics
over synthetic fixtures, not a fraud probability or proof of wrongdoing.

## Identity and duplicate prevention

All digests use SHA-256 of `models.canonical_json(...).encode('utf-8')`, without a
trailing LF. The source/manifest hashing contract itself is unchanged.

- `group_key`: canonical object with `dataset_id` and sorted distinct `account_ids`.
  Case ID is `inv_` + group_key. The seed refund, discovery depths, physical graph
  IDs, runtime timestamps, and priority do not enter group identity.
- Assessment key: canonical object with group key, input revision, inclusive
  window object, rule version, and the complete scoring configuration. Assessment
  ID is `asm_` + that digest. Including scoring configuration avoids treating a v1
  threshold/point override as the same assessment. Resource/display caps are not
  analytical configuration when the full result is COMPLETE.
- `analytical_hash`: fingerprints group-level measurements, findings, canonical
  source records, independent device counts, and semantic order/refund paths.
  Seed-context/discovery witnesses and physical IDs are excluded. A reused key
  with different analytical content is rejected instead of overwriting history.
- Evidence identity uses rule/version, source IDs, window, measured details,
  query/path provenance, and scoring configuration. Its provenance comes from
  complete candidate order/refund queries, not seed-specific context. Another
  seed producing the same group/evidence therefore reuses Evidence as well.

The immutable snapshot retains the **first** request's seed, discovery depths,
complete seed paths, full measurements/thresholds, findings, mitigating context,
and source records. A different seed can have different depths/witnesses without
creating another assessment. A retry from a different seed resumes the original
staged snapshot. Physical driver IDs are removed from saved paths; stable source
IDs, properties, relationship types, direction, and query IDs remain.

New evidence/input revision or a new window/configuration appends another
assessment to the same exact group. Duplicate requests for an old completed
assessment never move the case's latest pointer backward. A newly published
explicit historical assessment becomes latest because the user requested that
assessment; this is distinct from replaying an already saved assessment.

## Case policy and reviewer separation

| Input | Action |
| --- | --- |
| All current facts covered by applicable benign review | ALREADY_REVIEWED |
| CLOSED + new unexplained qualifying facts | REOPENED, same case |
| COMPLETE MEDIUM/HIGH, no exact managed group | CREATED, status OPEN |
| COMPLETE LOW, no published exact group | NO_CASE_REQUIRED |
| COMPLETE new assessment for an existing exact group, including LOW | UPDATED |
| Same complete assessment already published | EXISTING_UNCHANGED |
| INCOMPLETE, invalid, stale, or unacknowledged persistence | NOT_CREATED_INCOMPLETE |

An applicable managed case has the same dataset/group key and exact membership.
Membership never changes in place. A changed qualifying group gets its own case
and one OVERLAPS edge per overlapping case (stored lower case ID → higher ID,
read in either direction). Both cases get `requires_review=true`. No silent merge
occurs and previous assessment membership stays intact.

A LOW changed group creates no case or assessment, but reports overlapping IDs
and marks those existing cases `requires_review=true`; its returned assessment
explains the changed group. The flag preserves status, scores, reviews, and prior
membership. Later review tooling can decide how to handle such changes.

Case reviewer `status` and publication state are separate. Existing reviewer status is preserved, except CLOSED changes to REOPENED when
COMPLETE MEDIUM/HIGH evidence contains material new unexplained facts. Unchanged
benign-covered evidence returns ALREADY_REVIEWED; LOW/incomplete never reopens.
The writer itself rechecks memory under the shared lease. Closure and other reviewer
status changes require explicit human review actions. Existing Review nodes,
HAS_REVIEW links, and ABOUT links are preserved. Case policy creates no human Review. An account never receives a permanent abuse/benign status.

The default read-only JSON retains the investigation result shape. With
`--create-case`, a `case_action` object is added with action, case/assessment IDs,
reason, overlap IDs, and review flag. Exit 3 indicates incomplete analysis or
unconfirmed persistence, 1 unavailable data, 2 invalid input, and 0 a confirmed
business outcome (including NO_CASE_REQUIRED). A lost final acknowledgement can
have an unknown write outcome: the service checks the stored READY marker when
possible; otherwise it reports uncertainty and retry resolves it safely.

## Storage and publication protocol

See [graph-schema.md](graph-schema.md) for the additive fields/relationships.
`CaseRecord` extends the original Investigation model. `Assessment.snapshot_json`
is a validated canonical JSON object string; it contains evidence/source/path
snapshots and configuration. Nested dictionaries are never stored directly as
FalkorDB properties. Source IDs, timestamps, currency minor units, and import
state remain unchanged. Runtime case publication timestamps are separate from
deterministic fixture events.

The writer validates COMPLETE measurements/evidence with the deterministic scorer,
then rechecks Dataset READY and successful input revision before any staging.
It acquires the same renewable 120-second graph writer lease used by ingestion,
checks the revision again, and enforces existing identity/hash/membership rules.
Only fixed parameterized queries are executed.

1. MERGE a new LOADING Investigation header if necessary and a LOADING Assessment
   containing the immutable snapshot. Existing published case headers remain READY.
2. MERGE INCLUDES, Evidence, Assessment HAS_EVIDENCE, and any overlap links.
   Existing immutable content must match. Verify all matched endpoint counts,
   membership, assessment link, exact evidence content/cardinality, and overlaps.
3. Recheck READY/revision. One final query guards the READY dataset/horizon and
   staged analytical hash, publishes the HAS_ASSESSMENT attachment and Assessment READY,
   updates the case's current score/priority/time and latest property/edge, adds
   compatible case HAS_EVIDENCE links, and applies review flags. Reviewer status
   remains untouched unless the new-evidence reopening policy applies. The saved case is read back and verified.

The milestone uses **idempotent writes plus completion markers**, not an assumed
Neo4j transaction API or a cross-query transaction. Query parameter/deadline and
nullable-match behavior were checked against official FalkorDB documentation and
exercised on v4.20.7:
[GRAPH.QUERY](https://docs.falkordb.com/commands/graph.query) and
[OPTIONAL MATCH](https://docs.falkordb.com/cypher/optional-match).

A query failure or process crash can retain LOADING records. Case list excludes
unpublished headers; case show requires a published header and a consistent READY
latest assessment. Published history excludes LOADING snapshots and separately
reports their IDs. An interrupted update keeps the previous completed assessment
visible. Retry MERGEs without duplicating entities/edges or replacing the first
snapshot. A genuinely killed process may leave the lease until its 120-second TTL.

If source imports advanced while persistence was interrupted, reassess first.
The old unfinished snapshot cannot be published against a stale revision. A fresh
qualifying assessment can publish under a new key, leaving stale LOADING history
explicitly pending. No destructive cleanup is attempted.

Case HAS_EVIDENCE remains compatible as the union of completed history. Case
readers validate evidence against **each individual assessment** and never sum
contributions across history. They verify latest-reference/property consistency,
immutable group membership, source/evidence snapshot hashes, and per-assessment
score. Snapshot/review history remains readable from a fresh application process.
Legacy Investigation nodes without publication metadata are preserved but not
adopted as managed cases; they need an explicit migration before this service
can list/update them.

This is a single-writer MVP. The shared lease coordinates ordinary case/import
writers but is not fencing or a general concurrent-review/ingestion guarantee.
Investigation and case-reader multi-query reads are not snapshots; inconsistent
read state is rejected and can be retried. Native uniqueness constraints are not
installed or claimed. Large-graph performance and power-loss recovery are outside
the verified scope.

## Initialization, checks, and later agent tools

Run `uv run ringlens schema init` on the existing graph. It adds the missing range
indexes (30 total) and is idempotent; it never seeds or replaces nodes/edges.
The 20 → 30 and 25 → 30 upgrades are tested with existing records intact.

```sh
uv run pytest tests/unit
uv run pytest tests/integration/test_cases.py tests/integration/test_investigation.py --run-integration
uv run pytest --run-persistence
uv run ruff check .
uv run ruff format --check .
```

The implementation-status document records the final actual counts/outcomes.
Tests cover both patterns and household base/append behavior, MEDIUM creation,
OPEN/CLOSED LOW reassessment, alternate seeds, retries/crashes/acknowledgement
loss, current-input guards, duplicate-path counts, exact boundaries/rolling bursts,
global device users, read-only operation, review preservation, fresh-process case
readers, schema upgrade, and a controlled device change with unchanged amounts/times.
All graph mutations/cleanup use owned disposable graphs, never the application
graph or named volume. The existing persistence test briefly restarts the local
database; run it without concurrent client work.

Services available for the later tool-calling agent (all take the existing
`GraphStore`, configured with Settings/graph override):

| Service | Result |
| --- | --- |
| `investigator.investigate(store, refund_id, as_of=None, config=None)` | Read-only InvestigationResult with graph evidence and deterministic priority |
| `cases.apply_case_policy(store, result)` | CaseAction; fresh-input check, publication, deduplication, history |
| `cases.list_cases(store, limit=100)` | CaseList with published summaries, truncation, pending count |
| `cases.get_case(store, case_id)` | CaseDetail with completed snapshots/evidence, review history, overlaps, pending IDs |

Compose investigate/apply-case for an explicit mutation tool; pass the internally
computed result, not agent-invented score/priority fields. Keep raw Cypher and
evaluation files outside tool inputs. `scoring.score_records()` remains a pure
internal arithmetic service. The [review](reviews.md) and [agent](agent.md) services now add these workflows,
without exposing raw assessment payloads or human-review mutation to the LLM.
