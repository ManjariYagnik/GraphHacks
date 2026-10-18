# 02 · Shared household resources without escalation

**Problem.** Shared devices and shipping addresses can have ordinary
explanations. A graph investigation should show the connection and its limits,
without automatically treating the people as proven abusers. [data.json](data.json)
contains synthetic records for three accounts, one shared device and address,
15 orders, and three refunds on separate dates.

**Input and assumptions.** Seed refund `ref_4d44e5cc3e0a8dae77fd`; base horizon
`2026-10-15T10:00:00Z`. Every account has five orders and only one refunded order.
The label “household” is explanatory text here, never an operational source field.

**Steps.**

```sh
.venv/bin/python example/demo/demo.py --example 02 --dry-run
.venv/bin/python example/demo/demo.py --example 02
.venv/bin/ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --graph GRAPH_PRINTED_BY_RUNNER
.venv/bin/ringlens cases list --graph GRAPH_PRINTED_BY_RUNNER
```

The runner validates source rows, builds canonical manifests, initializes an
isolated graph, ingests and verifies the batch, then calls the existing
`investigate` and `apply_case_policy` services. FalkorDB discovers peers through
Account → Order → Device ← Order ← Account; addresses are supporting evidence,
not expansion links. `REFUND_CONTEXT_QUERY` and a fresh connection prove that
the selected refund and edges were persisted.

**Intermediate and final result.** Three candidates are found by expansion one.
R1 shared device adds 25 and R5 shared address adds 10; no synchronized burst or
elevated group refund rate is present. The result is **35/LOW** and
`NO_CASE_REQUIRED`. The policy does not create a new case. The verified graph
contains 3 accounts, 15 orders, and 3 refunds.

**Verification and edge cases.** Read the CLI's rule measurements and run
`ringlens graph verify --graph GRAPH_PRINTED_BY_RUNNER`. A shared address alone
cannot drive traversal, and missing/duplicate references are rejected before
write. If a human later reviews these facts, use the explicit reviewer workflow;
the demo runner does not invent a human decision. The example demonstrates
mitigating context and limits on automatic escalation.
