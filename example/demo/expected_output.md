# Verified demo output and what varies

The following results came from live integration tests against the local pinned
FalkorDB v4.20.7 service on isolated graphs. Each test imported the records,
verified the graph and case policy, reopened a database connection, read records
back, and deleted only its own test graph. The selected example 04 was also run
as the documented standalone command; that run left its graph in place for
inspection. No paid LLM call was part of these checks.

| Example | Base source counts (accounts/devices/addresses/orders/refunds) | Candidate accounts, maximum depth | Score and rules | Case action |
| --- | --- | --- | --- | --- |
| 01 | 1 / 1 / 1 / 5 / 1 | 1, depth 0 | 0/LOW; none | NO_CASE_REQUIRED |
| 02 | 3 / 1 / 1 / 15 / 3 | 3, depth 1 | 35/LOW; R1 +25, R5 +10 | NO_CASE_REQUIRED |
| 03 | 4 / 1 / 4 / 20 / 12 | 4, depth 1 | 90/HIGH; R1 +25, R2 +25, R3 +25, R4 +15 | CREATED |
| **04** | **5 / 2 / 5 / 25 / 15** | **5, depth 2** | **90/HIGH; R1 +25, R2 +25, R3 +25, R4 +15** | **CREATED** |
| 05 base → append | 3 / 1 / 1 / 15 / 3 → 9 refunds | 3, depth 1 | 35/LOW (R1/R5) → 100/HIGH (R1–R5) | CREATED after append |

For example 04, the starting refund is
`ref_9082163771ef0a5e5b26`. One actual depth-two path returned by FalkorDB was:

```text
acct_6b6c2003c30323e8969f
  → ord_154b00a084c1ef7789a8
  → dev_e8275d89e4f7ef6b4c53
  ← ord_6175a8af2caa394a31b4
  ← acct_4218e44441a264b4ae83  (bridge)
  → ord_42be13332699ab515039
  → dev_4be10c08732e1da6d452
  ← ord_20215c21031d47a78214
  ← acct_8b3a69241fb41aaed4fe
```

The path notation above is for human reading; the service returns the exact
directed relationship types and entity IDs in the structured assessment. The
stable case ID for this exact candidate group is
`inv_424afacab8e9c9bbf84f504eef395356c83203d5db6d22d30550676c2e1aa670`.
The standalone run reported `CREATED`; its immediate repeat returned
`EXISTING_UNCHANGED`. `get_case()` read back OPEN/READY status, score 90, all
five members, and the latest assessment. A fresh connection read the graph and
case again. The runner's `ringlens_example_<uuid>` graph name changes every
run, so copy the printed value when using CLI or Streamlit.

For example 05 with the explicit synthetic human review flags, the live test
read back the R1/R5 review, then found **2 benign-covered** and **6 material-new**
evidence facts after the six-refund append. It created an OPEN case at 100/HIGH
without deleting the earlier review. These counts describe this fixture only.

Dry-run output says `DRY RUN ONLY` and gives validated source counts plus
deterministic batch IDs. It does **not** show a graph score or persistence proof.
`--with-agent` is opt-in and paid. A live provider's tool sequence/status depends
on its response and credentials; no particular live AgentRun result is asserted
here. If a provider run is incomplete, the runner exits nonzero and says so.
