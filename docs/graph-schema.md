# RingLens graph schema 1.0

FalkorDB is the authoritative runtime store. Graph relationships determine the
connections available to investigations. Phase 2 defines schema and validation;
generation, importing, scoring, and case workflows remain future work.

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

## Future case memory (defined, never seeded)

All fields below are stored properties. Related entity IDs use the relationships
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

The future case writer must check endpoints, avoid duplicate edges, and ensure a
review's evidence belongs to the investigation containing that review. No case
state-transition engine is implemented in this phase.

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

Range indexes cover `id` on all ten labels, plus:

| Label | Timestamp indexes |
| --- | --- |
| Account | `created_at` |
| Order | `placed_at` |
| Refund | `requested_at` |
| Investigation | `assessment_as_of`, `updated_at` |
| Evidence | `window_start`, `window_end` |
| Review | `reviewed_at` |
| Dataset | `as_of` |
| ImportBatch | `started_at` |

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
