# 03 · Coordinated refunds on one shared device

**Problem.** Four synthetic accounts use one device on their orders and each
requests three refunds across three short bursts. Their shipping addresses
differ, so address sharing is not needed to find the connection. All canonical
rows are in [data.json](data.json).

**Input and assumptions.** Seed refund `ref_2b42ec18e21a70bdfa15`; base horizon
`2026-10-15T10:00:00Z`. The subset contains 4 accounts, 1 device, 4 addresses,
20 orders, and 12 refunds. Times and INR paise come from the deterministic
synthetic fixture and satisfy the 30-day inclusive assessment window.

**Steps.**

```sh
.venv/bin/python example/demo/demo.py --example 03 --dry-run
.venv/bin/python example/demo/demo.py --example 03
.venv/bin/ringlens investigate --refund-id ref_2b42ec18e21a70bdfa15 --graph GRAPH_PRINTED_BY_RUNNER --format json
.venv/bin/ringlens cases list --graph GRAPH_PRINTED_BY_RUNNER
```

The runner reuses `make_batch`, `validate_batch`, `ingest_batch`, `verify_graph`,
`investigate`, `apply_case_policy`, and `get_case`. FalkorDB performs refund
context and device-path queries; Python deduplicates events and applies the
versioned v1 score to graph-returned rows. A case is published only after READY
and revision checks.

**Intermediate and final result.** Expansion one finds all 4 accounts. R1, R2,
R3, and R4 contribute 25 + 25 + 25 + 15 = **90/HIGH**; R5 does not trigger.
Case policy reports `CREATED` and a deterministic `inv_...` ID. A second policy
call returns `EXISTING_UNCHANGED`. The runner reads back the case's OPEN/READY
status, 90 score, membership, and latest assessment, then reconnects to confirm
source/case persistence.

**Verification and edge cases.** Compare the CLI evidence paths and case detail
with the runner's output. The device has no unrelated users in this standalone
example; the global-user check is still executed by the production investigation
query. Multiple paths do not multiply order/refund counts. Invalid source
references stop import before writes; an incomplete traversal produces no final
score or case. This example highlights transparent scoring from graph-connected
records, though the bridge example has a clearer two-step graph story.
