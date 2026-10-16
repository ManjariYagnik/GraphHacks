# RingLens source data contract 1.0

This contract guides a future generator/importer. Phase 2 includes strict models,
pure validation, and hash helpers, without generating or importing a dataset.
All future demo records must be synthetic.

## Exact files and records

Each UTF-8 JSONL file contains one source object per line. Unknown fields are
errors, including planted scenario labels, scores, source hashes, and case outcomes.

| File / `kind` | Required fields |
| --- | --- |
| `accounts.jsonl` / `accounts` | `id`, `created_at` |
| `devices.jsonl` / `devices` | `id` |
| `addresses.jsonl` / `addresses` | `id`, `label` |
| `orders.jsonl` / `orders` | `id`, `account_id`, `device_id`, `address_id`, `placed_at`, `amount_minor`, `currency` |
| `refunds.jsonl` / `refunds` | `id`, `account_id`, `order_id`, `requested_at`, `amount_minor`, `currency` |

Use `Account`, `Device`, `Address`, `Order`, and `Refund` models. Their ID prefixes
are `acct_`, `dev_`, `addr_`, `ord_`, `ref_`, followed by a nonempty ASCII token
beginning with a letter/digit and continuing with letters, digits, `_`, or `-`.
Timestamps are integer UTC Unix seconds in 0..2^63−1; positive amounts are integer
minor units in 1..2^63−1; currency is exactly `INR`. Booleans, numeric strings, and
floating-point values are invalid integers. No trimming/coercion occurs. Amount
12500 means ₹125.00. Address labels use `Synthetic address <token>` and are invented.

Illustrative valid records (each belongs in its corresponding file):

```json
{"created_at":100,"id":"acct_a"}
{"id":"dev_a"}
{"id":"addr_a","label":"Synthetic address A01"}
{"account_id":"acct_a","address_id":"addr_a","amount_minor":12500,"currency":"INR","device_id":"dev_a","id":"ord_a","placed_at":200}
{"account_id":"acct_a","amount_minor":5000,"currency":"INR","id":"ref_a","order_id":"ord_a","requested_at":300}
```

References become [graph-schema.md](graph-schema.md)'s directed edges rather than
duplicated foreign-key properties. They remain in the complete source row's hash.

## Manifest

`manifest.json` contains exactly:

| Field | Rule |
| --- | --- |
| `schema_version` | Literal string `1.0` |
| `dataset_id` | Literal string `ringlens_demo_v1` |
| `batch_id` | `batch_` followed by the derived lowercase SHA-256 content hash |
| `mode` | `base` or `append` |
| `seed` | Strict integer in 0..2^63−1 |
| `as_of` | Strict integer UTC Unix seconds in 0..2^63−1 |
| `files` | Exactly five entries, ordered accounts, devices, addresses, orders, refunds |

Each file item contains exactly `kind`, `path`, `count`, and `sha256`. `path` is
exactly `<kind>.jsonl`, relative to the manifest directory. `count` is a nonnegative
strict integer and `sha256` is 64 lowercase hex characters. Absolute/traversal paths,
aliases, repeated/missing kinds, and extra kinds are invalid. Empty kinds retain a
file entry with count zero and SHA-256 of empty bytes.

`Manifest`/`ManifestFile` validate structure; `validate_manifest()` verifies batch
identity. `validate_base_batch()` additionally checks a complete record set,
cutoff, and counts. It does not read files or check their actual byte digests; the
future reader must perform those checks before graph writes.

## Canonical serialization and non-circular hashes

`models.canonical_json()` sorts string object keys lexicographically, uses compact
`,`/`:` separators, literal Unicode encoded as UTF-8, and standard JSON escaping.
No BOM, insignificant whitespace, NaN/infinity, unpaired surrogates, or duplicate
object keys are allowed. Unicode is preserved without normalization. Source values
are strings/integers, so floating-point encoding never enters source hashes.
Generated source timestamps derive deterministically from the explicit seed/as_of
and inputs, never the wall clock.

Build artifacts in this order:

1. Validate records. Sort each file by case-sensitive ASCII `id`, ascending. Reject
   repeated IDs, including identical duplicate rows.
2. Encode each row using `canonical_record_bytes()`, including exactly one LF byte
   (`\n`) per row. No blank lines, CRLF, or extra trailing newline. Empty files have
   zero bytes. SHA-256 the exact complete file bytes to obtain each file digest.
3. Form a descriptor with `schema_version`, `dataset_id`, `mode`, `seed`, `as_of`,
   and the ordered `files` metadata. **Exclude `batch_id`.** Canonicalize to UTF-8
   JSON **without a final LF**. SHA-256 these bytes to obtain `content_hash`;
   `manifest_content_hash()` implements this step.
4. Set `batch_id = "batch_" + content_hash`. Canonicalize the complete manifest
   with one final LF. Its own file hash is not used in its identity.

Source rows never embed batch IDs. File bytes determine file hashes; file hashes
and descriptor metadata determine content hash; content hash determines batch ID.
There is no circular dependency. Changing seed, mode, cutoff, count, or file digest
changes identity. File-array order is fixed, independent of filesystem traversal.

`source_hash(record)` hashes the complete canonical UTF-8 source row **including its
final LF**. It includes foreign-key IDs and excludes importer-managed fields. The
importer computes and stores it on Account, Device, Address, Order, and Refund
nodes. Source files cannot supply or override it.

## Validation and future importer obligations

Run individual validation, then separate cross-record validation:

```python
from ringlens.validation import SourceRecords, validate_base_batch

# records: parsed objects from the five files; manifest: a validated Manifest
snapshot = SourceRecords.model_validate(records)
validate_base_batch(snapshot, manifest)
```

Reject the candidate batch before writing source nodes if:

- Any ID repeats or any referenced account, device, address, or order is absent.
- Account creation is after order placement, or refund request precedes placement.
- Refund account differs from owner, currencies differ/are not INR, refund amount
  exceeds order amount, or more than one refund references an order.
- Any base event (`created_at`, `placed_at`, `requested_at`) exceeds manifest `as_of`.
- File paths/bytes/order/counts/digests, manifest identity, schema, or dataset ID
  violate the contract. The future file reader must also reject duplicate JSON keys
  and symlinks escaping the manifest directory; file I/O validation is not implemented.

Enforce one dataset per graph. A base importer must reject an unrelated dataset or
conflicting records, never clear a graph to load data. An identical completed batch
is an explicit no-op replay. A reused source ID with an identical `source_hash`
represents the same immutable source; a different/missing hash is a conflict.
Never overwrite conflicting properties or edges. In-file duplicates are always
invalid, even when their contents match.

Append validation needs **existing graph state plus candidate additions**. References
may resolve to existing nodes; owner, amount, timing, and one-refund rules must
cover both sets. Identical replays may be omitted from the candidate snapshot only
after hash checks. Append `as_of` cannot move the dataset cutoff backward, and the
resulting snapshot's events must be at/before it. `validate_records(snapshot,
as_of=manifest.as_of)` validates a complete snapshot, never an isolated append
fragment. Reconciliation and importing are not implemented in this phase.

Use a single writer per graph, inspect stored IDs/hashes before mutating, and verify
edge cardinalities afterward. Range indexes do not enforce uniqueness or the
one-dataset rule. Set Dataset/ImportBatch to INGESTING/LOADING; publish READY only
after verifying counts, hashes, and topology. Mark failures FAILED; future runtime
consumers must require a READY dataset. Recover partial failures explicitly rather
than resetting graphs/volumes. Runtime metadata timestamps are allowed; metadata
is outside the deterministic source JSONL files.

Use only fixed labels/relationships and parameterized values. Never accept Cypher
or identifiers from source files. Evaluation scenario labels remain outside runtime
files and Docker contexts. Cases/evidence/reviews belong to later runtime work.
