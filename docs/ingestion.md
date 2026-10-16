# Validated FalkorDB ingestion

Phase 4 imports the canonical base and append batches into FalkorDB. Verification
reconstructs references from actual edges; smoke queries return genuine graph
paths. There is no alternate database, NetworkX graph, risk scoring, case automation,
LLM, API, or UI.

## Start, initialize, and generate

From the repository root, configure `.env` as described in README.md, then:

```sh
uv sync --locked
docker compose up -d --wait
uv run ringlens doctor
uv run ringlens schema init
uv run ringlens data generate --seed 42 --as-of 2026-10-15T10:00:00Z --output data/demo --evaluation-output evaluation/demo_ground_truth.json
```

Generation is optional when using existing fixtures. It writes evaluation metadata
separately. Ingestion never opens it or uses scenario membership. Only the manifest
and five named application files are accepted.

## Dry-run and import the base

```sh
uv run ringlens ingest --input data/demo/base --dry-run
uv run ringlens ingest --input data/demo/base
uv run ringlens graph verify
uv run ringlens graph stats
```

Dry-run validates file bytes and the complete candidate snapshot against existing
graph state. It creates no nodes, relationships, metadata, indexes, or lease keys;
an absent graph stays absent. A connection is required to check existing conflicts
and references. Schema initialization is a separate additive command.

Import returns structured JSON with batch ID, status, source counts, assessment
cutoff, and successful verification when applicable. Status is `DRY_RUN`,
`DRY_RUN_NO_OP`, `READY`, or `NO_OP`. A READY batch with identical content verifies
the current graph and returns a no-op without changing nodes, edges, or metadata
timestamps. Reimporting the original base after append never rolls back the cutoff
or removes appended refunds.

The base fixture has 50 accounts, 42 devices, 46 addresses, 250 orders, and 42
refunds. Source relationships are 250 PLACED, 250 USED_DEVICE, 250 SHIPPED_TO,
42 REQUESTED_REFUND, and 42 FOR_ORDER, plus one HAS_BATCH edge.

## Verification and actual paths

```sh
uv run ringlens graph verify
uv run ringlens graph stats
uv run ringlens graph paths --limit 5
```

Verification returns a JSON report and exits nonzero on integrity/readiness failure:

- Source IDs are unique, properties match strict models, and canonical source_hash
  agrees with actual graph properties and edge-derived references.
- Every order has exactly one owning account, device, and address. Every refund
  has exactly one requester and order, with correct endpoint labels/directions.
- Ownership, chronology, currency, amounts, one refund per order, and cutoff hold.
- Canonical relationships have no duplicates or orphan endpoints.
- Batch membership, counts, and canonical file digests match applied manifests;
  identical immutable reference replays are not double-counted.
- There is one dataset and one base batch, every batch has valid provenance/state,
  and Dataset.as_of matches successful batches. Unfinished imports fail verification.

Report `counts` uses distinct application IDs and `node_counts` exposes actual
node counts, so duplicates are not hidden. Stats is read-only and may inspect
partial data: it shows node/distinct-ID counts, all relationship counts, and Dataset
status/cutoff. Stats alone does not establish readiness.

`graph paths` requires a successfully verified READY dataset. It selects an actual
stored refund by ID when no seed is supplied. Optional flags are `--refund ref_...`,
`--account acct_...`, and `--limit 1..20`. Consult the human demo guide for specific
opaque seeds; the command never reads that guide or evaluation data. It returns:

1. Refund → order → device, with requesting-account and shipping-address paths.
   The requester must also be that order's owner.
2. Account → order → device ← peer order ← peer account. Both orders must be in
   the 30 days ending at Dataset.as_of; the starting account is excluded as a peer.

The Python API returns `falkordb.path.Path` objects. CLI JSON retains node/edge
database IDs, application IDs, labels, properties, and actual edge directions.
Peer counts use `count(DISTINCT peer.id)` over all matching paths, while the bounded
sample retains multiple explanatory paths for a peer. These are smoke queries,
not production investigation or scoring.

## Append new evidence

```sh
uv run ringlens ingest --input data/demo/new_evidence --dry-run
uv run ringlens ingest --input data/demo/new_evidence
uv run ringlens graph verify
uv run ringlens graph stats
uv run ringlens ingest --input data/demo/new_evidence
```

Append references resolve from FalkorDB plus incoming records, without a base-file
argument. Counts stay 50 accounts/250 orders and become 48 refunds. Source
relationships total 846, with two HAS_BATCH edges. Dataset.as_of advances to
`2026-10-17T11:00:00Z` only upon successful publication. Identical append replay
returns NO_OP at 48 refunds.

Investigation, Evidence, Review, and their relationships are never written,
deleted, or recreated. A minimal valid case/review fixture in the integration
suite proves it survives append unchanged. Case automation remains deferred.

## Validation, writes, and provenance

Before graph writes, the importer verifies exact filenames/kinds/schema/dataset
ID, canonical UTF-8 JSON/LF, manifest/batch identity, sorted unique IDs, checksums,
counts, strict fields/types, and every source/cross-record rule. Absolute/traversal
paths and symlinked files are rejected. Conflicting duplicates, missing endpoints,
changed immutable source IDs, second refunds, foreign dataset assignment, and
non-advancing new append cutoffs fail before source/metadata writes.

New base imports require an unassigned graph without unmanaged source nodes.
Unrelated/case nodes and existing indexes are preserved. A different second base
is rejected; additions use append. No graph reset or clear operation is provided.

Write order is Account, Device, Address, Order, Refund, then edges. Fixed-label
queries receive parameterized chunks of at most 50 records. Nodes MERGE by stable
ID; immutable properties/source_hash are assigned only ON CREATE. Existing content
is checked, never overwritten. Edges MERGE from validated references. Every edge
write must match the expected row count, then full graph verification must pass,
so missing MATCH endpoints cannot silently lose records.

Dataset/ImportBatch node properties preserve the Phase 2 models. ImportBatch's
dataset_id remains its HAS_BATCH edge; null completed_at/error fields are omitted
as graph properties and reconstructed as null by the reader. HAS_BATCH adds two
importer-managed provenance properties: `manifest_json` (canonical Manifest JSON
string without final LF) and `source_ids` (distinct IDs supplied in that batch).
They allow verification without external files. No source bodies are cached:
canonical source records are reconstructed from actual graph properties and edges.
The file and node-model contracts are unchanged.

## Failure and single-writer recovery

The multi-query import is **not atomic**. One initial query marks Dataset INGESTING
and the batch LOADING and records provenance. After writes and complete graph
verification, one final query marks both READY and advances the cutoff together.
Other batch identities/history are retained. Readers must check Dataset.status
READY before treating an assessment as complete; source node presence is insufficient.

A handled failure attempts to mark Dataset/batch FAILED with a sanitized error.
Append failures retain the last successful horizon. A disconnect or process crash
may leave LOADING, never success. Retry the original command with the same files:
matching partial immutable nodes are retained, missing original nodes/edges are
completed, and the whole graph is verified again. Conflicting properties/edges or
different input are rejected, not overwritten as recovery.

Retry updates the same batch record, retaining started_at and other batch history.
If READY committed but its acknowledgement was lost, failure handling does not
downgrade it; replay verifies and returns NO_OP. Individual uncertain query writes
are never automatically replayed.

A fail-fast renewable 120-second lease uses a transient Redis key in the same
FalkorDB instance, scoped to graph name. It is coordination only; durable graph
metadata determines success. Dry-run/known READY no-ops do not acquire it. Preflight
is rechecked under the lease, renewed before each write. A persisted LOADING/FAILED
batch blocks a different batch even after expiry; resume the unfinished batch.
After a hard process kill, allow up to 120 seconds for its lease to expire before
retrying; no force-unlock or graph-reset command is provided.
The [official redis-py lock API](https://redis.readthedocs.io/en/stable/lock.html)
provides nonblocking acquisition and owned TTL renewal/release.

This is a single-writer MVP, not concurrent ingestion support. Run one writer per
graph and avoid schema/manual source changes during imports. Stop an old process
paused beyond the lease before recovery. This guard is not a fencing/transaction
system. No native uniqueness or distributed-transaction guarantee is claimed.

## Disposable graphs and checks

All new commands and `schema init` accept `--graph NAME`; omission uses configured
RINGLENS_GRAPH_NAME. Use a new name consistently for isolated manual tests. No CLI
command deletes graphs or volumes.

```sh
uv run pytest tests/unit
uv run pytest tests/integration/test_ingest.py --run-integration
uv run pytest --run-persistence
uv run ruff check .
uv run ruff format --check .
```

Integration tests own UUID-named graphs, refuse existing names, and clean up only
their own graphs. They cover rejection without writes, replay, partial failures,
crash/lease recovery, endpoint loss, content/topology/cutoff corruption, lost
acknowledgements, case preservation, and genuine paths. `--run-persistence` also
runs the existing orderly-restart marker check and briefly restarts the local
database. It deletes no volume and establishes no power-loss/backup guarantee.

`docker compose down` preserves data; resume with `docker compose up -d --wait`.
See implementation-status.md for actual verification results and limitations.
