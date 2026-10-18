# 04 · Bridge between two device groups (selected demo)

**Problem.** Two accounts on the starting device appear connected to one bridge
account. The bridge also used a second device; two more accounts are reachable
only through that second device expansion. A flat refund table would not expose
the full path. [data.json](data.json) contains only strict synthetic source rows.

**Input and assumptions.** Seed refund `ref_9082163771ef0a5e5b26`; base horizon
`2026-10-15T10:00:00Z`. The subset has 5 accounts, 2 devices, 5 addresses,
25 orders, and 15 refunds. IDs and relationships are preserved from the
repository's deterministic fixture; no evaluation labels enter the graph.

**Steps.**

```sh
.venv/bin/python example/demo/demo.py --example 04 --dry-run
.venv/bin/python example/demo/demo.py --example 04
.venv/bin/ringlens investigate --refund-id ref_9082163771ef0a5e5b26 --graph GRAPH_PRINTED_BY_RUNNER --format json
.venv/bin/ringlens cases show --case-id inv_424afacab8e9c9bbf84f504eef395356c83203d5db6d22d30550676c2e1aa670 --graph GRAPH_PRINTED_BY_RUNNER
```

The runner derives a canonical manifest, initializes 30 indexes on a new graph,
imports and verifies the source nodes/edges, and reads the seed path back with
`REFUND_CONTEXT_QUERY`. `investigate` sends fixed parameterized FalkorDB queries:
seed context, first device expansion, second device expansion, event gathering,
and device-user totals. `apply_case_policy` publishes an immutable Assessment,
Evidence, and Investigation via the existing single-writer service. `get_case`
checks the published snapshot and memberships.

**Intermediate and final result.** Expansion one reaches 3 accounts; expansion
two reaches all 5. A complete path to a distant account includes both devices
and the bridge account. R1 +25, R2 +25, R3 +25, and R4 +15 give **90/HIGH**.
The case action is `CREATED`; repeating the same policy action is
`EXISTING_UNCHANGED`. The runner verifies OPEN/READY, score, membership, and
latest assessment, then reconnects and reads the case and refund path again.

**Verification and edge cases.** Open the saved graph in Streamlit by launching
the app with `RINGLENS_GRAPH_NAME=GRAPH_PRINTED_BY_RUNNER`; in **Investigate**
choose the seed refund and highlight a depth-two path. The short graph labels
map to canonical IDs in the visible ID key. CLI `graph verify` and `cases show`
provide independent checks. If the graph is unavailable, `--dry-run` validates
records but cannot claim traversal, score, or persistence. The runner refuses an
already existing graph. This is the clearest demonstration that FalkorDB's
relationships, rather than a label in a fixture, determine discovery.
