# 05 · Human review, later refunds, and fresh evidence

**Problem.** The shared-resource group from example 02 initially has a plausible
low-priority pattern. Six later refunds create two synchronized bursts. A prior
human explanation of device/address sharing should remain in memory but must
not excuse the new transactions. [data.json](data.json) contains a base subset
and a refund-only append segment. Both become standard five-file manifests in
the runner; no Review or case record is embedded in source data.

**Input and assumptions.** Seed refund `ref_4d44e5cc3e0a8dae77fd`.
The base horizon is `2026-10-15T10:00:00Z`; the append advances it to
`2026-10-17T11:00:00Z`. Base counts are 3 accounts, 15 orders, 3 refunds;
append adds 6 refunds on distinct previously unrefunded orders. A human must
explicitly provide any review outcome and explanation. Omitting the review flags
runs the append demo without manufacturing a reviewer decision.

**Steps.** The following command represents an explicit synthetic test review,
not an agent-produced or inferred human decision:

```sh
.venv/bin/python example/demo/demo.py --example 05 --dry-run
.venv/bin/python example/demo/demo.py --example 05 \
  --reviewer-id demo-reviewer \
  --review-outcome BENIGN_EXPLANATION \
  --review-explanation "Shared device and address explained; later refunds are not cleared."
.venv/bin/ringlens reviews history --refund-id ref_4d44e5cc3e0a8dae77fd --graph GRAPH_PRINTED_BY_RUNNER
.venv/bin/ringlens cases list --graph GRAPH_PRINTED_BY_RUNNER
```

The runner validates the base, imports and verifies it in FalkorDB, investigates
the seed, and calls `record_review` on R1/R5 only because the human supplied all
three review fields. The review service saves an Assessment/Evidence snapshot
without creating a LOW case. `ingest_batch` then adds only the six Refund nodes
and derived edges; `verify_graph` checks the 9-refund snapshot. `investigate`,
`compare_evidence`, and `apply_case_policy` operate on current graph state.

**Intermediate and final result.** Base: 3 candidates, R1/R5, **35/LOW**.
After append: 3 candidates, all R1–R5, **100/HIGH**. In the tested explicit-review
run, comparison found 2 benign-covered facts and 6 material-new facts; the
case action was `CREATED`. The review remains readable after reconnecting, as
does the new OPEN/READY case and the 9-refund graph. This is evidence evolution,
not automatic reversal of a human decision or proof of abuse.

**Verification and edge cases.** Read review history and the case through their
CLI commands, or inspect the **Cases & Reviews** page after selecting the graph.
An invalid second refund for an order, orphan order/account, incorrect currency,
or append horizon that does not advance is rejected before writes. If an import
fails partway, the dataset is not presented as READY; retry its original batch.
This workflow shows persistent, scoped human memory alongside new graph events.
