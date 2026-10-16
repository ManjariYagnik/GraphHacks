# RingLens synthetic demo scenarios

This is a human walkthrough and fixture specification, not agent input. It is
excluded from Docker build contexts alongside `evaluation/` and the implementation
status document, which also lists human demo seeds. No records have been
ingested and no investigation, review, scoring engine, or LLM is created in Phase 3.

## Generate and validate

Run from the repository root; no FalkorDB server, credentials, or LLM is required:

```sh
uv run ringlens data generate --seed 42 --as-of 2026-10-15T10:00:00Z --output data/demo --evaluation-output evaluation/demo_ground_truth.json
uv run ringlens data validate --input data/demo/base
uv run ringlens data validate --input data/demo/new_evidence --base data/demo/base
uv run pytest tests/unit/test_synthetic.py
```

If uv is not on PATH, use `.venv/bin/ringlens` and `.venv/bin/pytest`, or the local
toolchain setup in `implementation-status.md`. `--as-of` is required, must explicitly
be UTC, and must use whole seconds. Seed is a nonnegative signed 64-bit integer;
the generator requires enough history after the Unix epoch for account timestamps.
The date/time above is a fixed fixture parameter, independent of today's date.

Repeated generation with the same seed/as_of preserves identical files. Differing
existing outputs are refused before any file is written. Use another output path
or explicitly add `--overwrite` to replace generated files. The flag never removes
extra files: unexpected contents in a batch directory cause an error. Evaluation
output must be outside the operational output root and under an `evaluation/`
directory, so existing Docker exclusions also cover alternate output paths.

## Checked-in fixture

Base cutoff: **2026-10-15 10:00:00 UTC**. Append cutoff:
**2026-10-17 11:00:00 UTC**, after the final new refund event.

| Record | Base | Append additions | Combined snapshot |
| --- | ---: | ---: | ---: |
| Account | 50 | 0 | 50 |
| Device | 42 | 0 | 42 |
| Address | 46 | 0 | 46 |
| Order | 250 | 0 | 250 |
| Refund | 42 | 6 | 48 |

Both directories contain all five canonical JSONL files and a manifest. The append
contains empty account/device/address/order files, with count zero and the empty
SHA-256, as required by the existing five-file manifest contract.

All accounts have five orders. Orders fall on five dates between 21 and five days
before the base cutoff, with varied seconds and integer INR amounts. Accounts are
created before their first order. Refunds reference earlier, distinct orders and
never exceed order amounts. Every original order remains inside the advanced
30-day window, with several days of margin from its lower boundary. Canonical file
order is by opaque ID; event chronology is validated separately.

Account scenario memberships are shuffled with a local seeded random generator
**before** assigning IDs. All entity IDs use type prefixes plus opaque 20-hex-digit
tokens derived deterministically from seed/type/ordinal; no scenario enters an ID.
Address labels contain only `Synthetic address <opaque token>`. Operational files
contain no memberships, fraud flags, risk scores, descriptions, or grouping tags.

## Human starting refunds

These IDs are for seed 42 and the fixed cutoff above. Use them in a later
investigation demo once ingestion and investigation tools exist:

| Scenario | Starting refund | Expected accounts after expansion 1 → 2 |
| --- | --- | --- |
| Ordinary control | `ref_ef82ea0a22e9291d2977` | 1 → 1 |
| Coordination pattern A | `ref_2b42ec18e21a70bdfa15` | 4 → 4 |
| Coordination pattern B | `ref_9082163771ef0a5e5b26` | 3 → 5 |
| Shared household | `ref_4d44e5cc3e0a8dae77fd` | 3 → 3 |

An expansion here means accounts → devices used on in-window orders → accounts
using those devices on in-window orders. It includes already discovered accounts.
These expectations are fixture assertions, not an implementation of discovery or
scoring. Runtime discovery must later traverse FalkorDB's actual edges.

## Scenarios

| Group | Accounts | Base refunds | Per-account refunded-order fraction | Synchronized dates |
| --- | ---: | ---: | --- | ---: |
| Ordinary | 38 | 12 | Twelve at 1/5; twenty-six at 0/5 | 0 |
| Pattern A | 4 | 12 | Each 3/5 | 3 |
| Pattern B | 5 | 15 | Each 3/5 | 3 |
| Household | 3 | 3 | Each 1/5 | 0 |

Ordinary customers each have an exclusive device. Two pairs share addresses as
noise; remaining ordinary addresses are distinct. The twelve ordinary refunds
occur on distinct UTC dates. Even combined with base household refunds, they do
not accidentally form a three-account synchronized pattern.

Pattern A uses one exclusive shared device with a different address for each
member. Three separate UTC dates each have one refund per member within at most
20 minutes. Shared address is unnecessary for discovery.

Pattern B uses two exclusive devices. A/B use D1; C bridges D1 and D2 through
different orders; D/E use D2. The selected seed belongs to A on a D1 order. The first
expansion reaches A/B/C; only the next expansion through C's D2 orders reaches D/E.
All device uses needed for these expansions are inside the 30-day window. Each
member refunds three distinct orders in three synchronized dates.

For this cutoff, both planted patterns have bursts on October 4, 8, and 13 UTC.
The household shares one exclusive device and address but has only one refund per
account, on September 25, September 29, and October 7 UTC. Shared infrastructure
alone does not establish abuse, and the fixture does not give accounts a permanent
status.

The append adds two household bursts: three refunds between October 16 10:00 and
10:20 UTC, and three between October 17 10:00 and 10:20 UTC. Each burst includes all
three accounts. Six previously unrefunded orders are used; no source accounts,
orders, devices, or addresses change. Each household account then has 3/5 refunded
orders and the group has two synchronized dates in the advanced window. This sets
up later reassessment; no case, evidence, or review is pre-created.

## Reproducibility, integrity, and separation

The generator follows the existing `data-contract.md` serialization/hash rules
without changing the models or manifest. Source rows sort by ID and JSON keys,
use UTF-8 and exactly one LF per row; empty files have zero bytes. The manifest
descriptor excludes `batch_id` from its content hash, then derives the batch ID
from that hash. There are no random UUIDs, clock defaults, or runtime timestamps in
fixtures. Tests compare all artifact bytes, alternate seeds, alternate cutoffs,
and independent interpreter hash seeds.

`data validate` checks contract integrity, strict record models, references,
chronology, refund limits, and cutoff consistency. Append validation requires a
supplied base and validates the combined candidate snapshot. It allows identical
immutable reference replays, rejects conflicting reuse, and forbids moving the
horizon backward. It neither writes to a graph nor reads evaluation data. Fixed
scenario sizes, bursts, and expansion expectations are independently checked by
fixture tests, not by a production scoring engine.

`evaluation/demo_ground_truth.json` contains membership lists, seed refunds,
expected expansions, burst counts, rate fractions, and household before/after
expectations. Only fixture/evaluation tests may consume it. Never import it into
FalkorDB, expose it through agent tools, or add it to operational manifests.
The generator writes it explicitly to a separate path. `evaluation/` (including
nested directories), this guide, and the implementation status document are
excluded from Docker build contexts. Future
runtime images and tools must preserve these boundaries.
