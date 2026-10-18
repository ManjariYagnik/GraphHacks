# 01 · Ordinary refund control

**Problem.** A refund is not evidence of coordination by itself. This control
checks that RingLens can follow a real order path without escalating an isolated
customer. All records in [data.json](data.json) are synthetic: one account, its
exclusive device/address, five INR orders, and one refund.

**Input and assumptions.** Seed refund `ref_ef82ea0a22e9291d2977`; base data
through `2026-10-15T10:00:00Z`. The JSON file is a demo envelope containing
strict canonical source records. The runner derives the five JSONL manifest
entries and batch hash; the envelope itself is never imported as a graph node.

**Steps.** From the repository root:

```sh
.venv/bin/python example/demo/demo.py --example 01 --dry-run
.venv/bin/python example/demo/demo.py --example 01
.venv/bin/ringlens investigate --refund-id ref_ef82ea0a22e9291d2977 --graph GRAPH_PRINTED_BY_RUNNER
.venv/bin/ringlens cases list --graph GRAPH_PRINTED_BY_RUNNER
```

Replace the placeholder with the fresh `ringlens_example_...` graph name printed
by the live run. The runner calls `make_batch`/`validate_batch`, `initialize_schema`,
`ingest_batch`, `verify_graph`, and `investigate`. FalkorDB stores Account, Order,
Device, Address, Refund and their directed edges. The refund-context query reads
those edges back; case policy is applied only after a COMPLETE assessment.

**Intermediate and final result.** The verified graph has 1 account, 5 orders,
and 1 refund. Discovery stays at depth zero. No v1 rule triggers: **0/LOW**;
policy returns `NO_CASE_REQUIRED`, with no case ID. The runner reconnects and
verifies source counts and the refund path. This is a demo heuristic result, not a
statement about the customer.

**Verification and edge cases.** Compare `ringlens graph verify --graph GRAPH` and
the CLI investigation with the runner's output. Missing references, changed
immutable IDs, invalid INR amounts, or events after `as_of` are rejected before
source writes by the existing validators. A missing database produces an explicit
failure; `--dry-run` still validates locally and claims no persistence. This
example shows that the graph can return a connected path while the policy avoids
creating a case for a routine refund.
