# RingLens graph schema 1.0

FalkorDB is the authoritative runtime store. Graph relationships determine the
connections available to investigations. The source contract remains 1.0. The
case and review milestones add Assessment/history/publication metadata without changing source
records, import metadata, fixture manifests, or the existing review model.

`models.py` uses strict Pydantic records with unknown fields forbidden. IDs are
case-sensitive strings with the prefix below and a nonempty ASCII suffix: first
character a letter/digit, followed by letters, digits, `_`, or `-`. Timestamps are
nonnegative integer UTC Unix seconds. Timestamps and amounts fit signed 64-bit
integers; amounts are positive minor units. Booleans, numeric strings, and floats
are rejected for integer fields. Currency is exactly `INR`.

## Source nodes and references

Canonical source JSON contains exactly the application fields in the data contract.
The importer adds `source_hash` to every immutable source node; source JSON cannot
supply it. The hash includes foreign keys even though those keys become edges.
`schema.source_node_properties()` implements this property projection and hash.

| Label | ID prefix | Stored properties besides `id`, `source_hash` | Source fields represented only by edges |
| --- | --- | --- | --- |
| Account | `acct_` | `created_at` | — |
| Device | `dev_` | — | — |
| Address | `addr_` | `label` | — |
| Order | `ord_` | `placed_at`, `amount_minor`, `currency` | `account_id`, `device_id`, `address_id` |
| Refund | `ref_` | `requested_at`, `amount_minor`, `currency` | `account_id`, `order_id` |

Address labels use `Synthetic address <token>`, for example `Synthetic address A01`.
Tokens follow the ASCII suffix rule above. They must be invented, never derived
from real addresses. This structural convention makes placeholders recognizable;
it does not classify arbitrary real-world strings as synthetic.

| Directed relationship | Source reference | Required cardinality |
| --- | --- | --- |
| `(Account)-[:PLACED]->(Order)` | `Order.account_id` | Exactly one owner per order |
| `(Order)-[:USED_DEVICE]->(Device)` | `Order.device_id` | Exactly one device per order |
| `(Order)-[:SHIPPED_TO]->(Address)` | `Order.address_id` | Exactly one address per order |
| `(Account)-[:REQUESTED_REFUND]->(Refund)` | `Refund.account_id` | Exactly one requester per refund |
| `(Refund)-[:FOR_ORDER]->(Order)` | `Refund.order_id` | Exactly one order per refund; at most one refund per order |

All endpoints must exist. Accounts may have many orders/refunds; devices and
addresses may be shared. Device use goes through an order, whose `placed_at`
provides its timing. No direct account-to-device usage edge is defined.

`validation.validate_records()` checks duplicate IDs, references, account creation
≤ placement ≤ refund request, matching owner/currency, refund amount ≤ order amount,
one refund per order, and every source event ≤ `as_of`. These cross-record checks
are separate from individual models. The input snapshot exists only for validation;
it is not a runtime graph substitute.

## Case memory (created only by explicit case requests)

The original models below are reused. Related entity IDs use the relationships
below rather than additional fields on these records.

| Label | Fields |
| --- | --- |
| Investigation | `id` (`inv_`), `status`, `priority`, `score`, `rule_version`, `assessment_as_of`, `created_at`, `updated_at` |
| Evidence | `id` (`ev_`), `rule_id`, `rule_version`, `window_start`, `window_end`, `contribution`, `source_ids`, `details_json` |
| Review | `id` (`rev_`), `outcome`, `explanation`, `reviewed_at` |

Investigation statuses: `OPEN`, `IN_REVIEW`, `CLOSED`, `REOPENED`. Priorities: `LOW`,
`MEDIUM`, `HIGH`. Score is a finite number in 0..100 (integer/float inputs accepted,
booleans/strings rejected). `updated_at >= created_at`. Contributions are finite
signed numbers; the model implements no scoring logic. Evidence windows are
inclusive, with `window_start <= window_end`. Rule IDs/versions and explanations
must contain non-whitespace text.

`source_ids` is a nonempty array of distinct source-record IDs. A future case writer
must resolve them against the graph and enforce its assessment cutoff.

Review outcomes: `BENIGN_EXPLANATION`, `NEEDS_MORE_INFORMATION`, `CONFIRMED_ABUSE`.
**No case or review outcome establishes an account's permanent status.** Account
has no status field.

Allowed case relationships:

- `(Investigation)-[:INCLUDES]->(Account)`
- `(Investigation)-[:HAS_EVIDENCE]->(Evidence)`
- `(Investigation)-[:HAS_REVIEW]->(Review)`
- `(Review)-[:ABOUT]->(Evidence)`

The case writer checks endpoints and avoids duplicate edges. Review nodes/links
are preserved. The explicit human review writer validates scope and endpoints;
policy-controlled reopening requires new unexplained qualifying evidence.

`details_json` is a string encoding a validated JSON object. Use
`evidence_details_json(details)` to explicitly encode a dictionary before assigning
it. Evidence rejects raw dictionaries in this field, invalid JSON, duplicate keys,
NaN/infinity, non-object roots, and invalid UTF-8. It canonicalizes key order and
whitespace. Nested objects remain inside the string, never nested graph properties.

The [FalkorDB property type documentation](https://docs.falkordb.com/datatypes)
and [v4.20.7 property-type definitions](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/src/value.h)
support the chosen integers, doubles, strings, and arrays of strings. Maps may be
query parameters/results but cannot be stored as nested properties. Null metadata
values remain explicit nulls in models but are omitted from graph properties with
`model_dump(exclude_none=True)`.

### Additive case publication and assessment history

`case_models.CaseRecord` extends the unchanged strict `Investigation` model with:
`dataset_id`, `group_key` (SHA-256), `publication_status` (LOADING/READY), optional
`latest_assessment_id`, and boolean `requires_review`. The stable case ID is
`inv_` plus the hash of dataset ID and sorted candidate account IDs. Its INCLUDES
membership is immutable; overlapping changed groups are not silently merged.
Reviewer `status` stays separate from publication state, score, and priority.

The new `Assessment` label has properties:

| Fields | Meaning |
| --- | --- |
| `id` (`asm_` + SHA-256), `dataset_id`, `group_key` | Stable assessment identity and parent group |
| `input_revision`, `window_start`, `window_end`, `rule_version`, `config_hash` | Successful import revision and analytical configuration |
| `score`, `priority`, `analytical_hash` | This assessment's result and seed-independent content fingerprint |
| `snapshot_json` | Validated canonical JSON string containing the full COMPLETE assessment, semantic paths, source records, measurements, and evidence |
| `overlap_case_ids` | String array of overlaps observed when this snapshot was staged |
| `status`, `created_at`, optional `completed_at` | LOADING/READY completion marker and runtime timestamps |

Snapshots and analytical fields are immutable; only LOADING → READY and completion
time change during publication. READY requires a completion time. Readers exclude
LOADING history and require the case's latest property/edge to agree with a READY
assessment and its score, priority, version, and horizon. Existing-case staging
leaves the prior completed assessment visible until publication succeeds.

Additional allowed relationship signatures:

- `(Investigation)-[:HAS_ASSESSMENT]->(Assessment)`
- `(Investigation)-[:LATEST_ASSESSMENT]->(Assessment)` (one published latest)
- `(Assessment)-[:HAS_EVIDENCE]->(Evidence)`
- `(Investigation)-[:OVERLAPS]->(Investigation)` (one edge, lower case ID to higher)

Existing Investigation INCLUDES and HAS_EVIDENCE remain supported. Case HAS_EVIDENCE
is the union of evidence from completed history; the current score comes only
from LATEST_ASSESSMENT. Reviews and ABOUT links are never rewritten. OVERLAPS is
read in either direction and sets `requires_review` without changing reviewer status.
The allowlist now has 12 labels and 15 distinct relationship types (16 signatures,
because HAS_EVIDENCE has two allowed source labels).

Booleans, scalars, and arrays of strings are supported by the pinned server and
live tests. Nested paths, records, thresholds, and measurements stay inside
validated JSON strings. See [cases.md](cases.md) for exact keys and retry behavior.

### Additive scoped human reviews and agent audit

`review_models.ScopedReview` extends the legacy four-field Review with:
`reviewer_id`, `dataset_id`, `assessment_id`, `rule_version`, `account_ids`,
`evidence_ids`, fact `fingerprints`, canonical validated `scope_json`, optional
`case_id`, explicit `case_status`, `supersedes`, `valid_until`, and
`publication_status` LOADING/READY. Immutable reviewed facts live inside JSON;
scalars and string arrays remain native properties. Legacy reviews remain readable
but cannot provide automated benign clearance without scope. No account status is added.

Additional relationships:

- `(Review)-[:REVIEWS_ASSESSMENT]->(Assessment)` (one complete snapshot)
- `(Review)-[:SUPERSEDES]->(Review)` (optional correction, append-only history)

Review ABOUT links select the reviewed evidence. A standalone reviewed LOW
Assessment need not have an Investigation parent. It may later be attached to a
qualifying case without duplicating the snapshot. HAS_ASSESSMENT edges now carry
`publication_status`: LOADING during case attachment, READY only in the final case
publication query. Missing markers on legacy links are treated as READY. This
prevents a previously completed standalone snapshot from leaking into a partially
published case update. Review and case writers share the ingestion writer lease.

`AgentRun` has `id` (run_ + UUID hex), `mode` (assess/investigate), execution
`status` (RUNNING/COMPLETE/INCOMPLETE/FAILED), `started_at`, optional `completed_at`,
string arrays `requested_refund_ids`, `requested_case_ids`, `assessment_references`,
`assessment_ids`, `case_ids`, validated canonical strings `activity_json` and
`errors_json`, and grounded `final_response`. Activity stores tool names/status,
relevant IDs/actions and sanitized errors. No raw prompt, raw tool arguments,
provider reasoning or credentials are retained. Audit is independent of case
publication and creates no source/review records. A crash-left RUNNING run is not
success. See [reviews.md](reviews.md) and [agent.md](agent.md).

## Import metadata

| Label | Stored properties | Edge-only field |
| --- | --- | --- |
| Dataset | `id`, `schema_version`, `as_of`, `status` | — |
| ImportBatch | `id`, `content_hash`, `mode`, `status`, `started_at`, optional `completed_at`, optional `error` | `dataset_id` |

`(Dataset)-[:HAS_BATCH]->(ImportBatch)` assigns each batch to exactly one dataset.
The importer must enforce one dataset per graph, ID `ringlens_demo_v1`, version
`1.0`. Dataset statuses: `INGESTING`, `READY`, `FAILED`. Batch statuses: `LOADING`,
`READY`, `FAILED`; modes: `base`, `append`.

Batch IDs are `batch_` plus their lowercase content hash. `LOADING` requires null
`completed_at`/`error`. Completed batches require `completed_at >= started_at`;
`FAILED` requires an error and `READY` requires null error. Store sanitized errors,
never credentials or raw payloads. Runtime metadata may use the clock; generated
source fixtures may not. Initialization creates no metadata instances.

## Initialization and enforcement

```sh
uv run ringlens schema init
uv run ringlens schema init
```

Initialization checks `GRAPH.LIST`, reads `db.indexes()` when the graph exists,
creates missing range indexes, and waits up to 30 seconds for them to be operational.
The first index can create an empty graph. No nodes, edges, or cases are seeded.
Existing data and indexes are never dropped or replaced. Completed indexes remain
after failure; rerun to finish. Run one initializer/importer per graph at a time.
A verified concurrently created index is accepted; unrelated errors still fail.

Range indexes cover `id` on all twelve labels, plus:

| Label | Additional indexes |
| --- | --- |
| Account | `created_at` |
| Order | `placed_at` |
| Refund | `requested_at` |
| Investigation | `assessment_as_of`, `updated_at` |
| Evidence | `window_start`, `window_end` |
| Review | `reviewed_at`, `reviewer_id`, `dataset_id`, `publication_status` |
| Dataset | `as_of` |
| ImportBatch | `started_at` |
| Assessment | `window_end`, `created_at`, `input_revision` |
| AgentRun | `started_at` |
| Investigation | `group_key` |

There are 30 range indexes. Upgrading the 25-index case graph adds AgentRun.id,
AgentRun.started_at, Review.reviewer_id, Review.dataset_id and
Review.publication_status. The original 20-index graph adds those plus the five
Assessment/case indexes. Repeating initialization creates zero indexes and no records.

**Indexes accelerate lookups; they do not enforce uniqueness.** Native `UNIQUE`
and `MANDATORY` support was checked in the pinned release's
[constraint helpers](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/tests/flow/constraint_utils.py).
It uses `GRAPH.CONSTRAINT`, with asynchronous validation and supporting indexes for
uniqueness. Phase 2 installs no native constraints. The importer must validate
mandatory fields, uniqueness, one dataset, immutable hashes, references, and
cardinalities before writing. Single-writer ingestion and checks against existing
graph state are required; indexes alone cannot make concurrent imports safe. Native
constraints may supplement this contract after live verification in a later phase.

DDL and introspection follow the pinned v4.20.7
[index syntax tests](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/tests/flow/test_index_create.py)
and [catalog source](https://github.com/FalkorDB/FalkorDB/blob/v4.20.7/src/procedures/proc_list_indexes.c):
`CREATE INDEX FOR (n:Label) ON (n.property)` and the catalog's per-property `types`
map. No Neo4j constraint syntax or `IF NOT EXISTS` support is assumed. Full-text or
vector indexes do not satisfy a required range index.

`schema.LABELS`, `RELATIONSHIPS`, and `RELATIONSHIP_TYPES` are fixed allowlists.
DDL identifiers come exclusively from the constant index list; input data never
supplies labels or property identifiers. Parameterize all runtime values.

## Refund context

`schema.REFUND_CONTEXT_QUERY` uses `params={"refund_id": "ref_..."}`:

```cypher
MATCH (account:Account)-[:REQUESTED_REFUND]->(refund:Refund {id: $refund_id})
      -[:FOR_ORDER]->(order:Order),
      (account)-[:PLACED]->(order),
      (order)-[:USED_DEVICE]->(device:Device),
      (order)-[:SHIPPED_TO]->(address:Address)
RETURN account.id, refund.id, order.id, device.id, address.id, order.placed_at
```

The shared account binding checks ownership. With the importer invariants this
returns exactly one row per refund. Future callers must report missing/multiple
rows as inconsistent data, not conceal violations with `DISTINCT` or `LIMIT`.
