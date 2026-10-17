# Persistent human review memory

Human decisions are append-only FalkorDB Review nodes. They explain a specific
set of observed facts; they never change source events, arithmetic, or account
status. Ordinary `investigate` remains read-only. The agent has no review-write
tool. Only the separate human CLI/service records an outcome or changes case status.

## Record and inspect

Use a fresh graph for the exact base → append demonstration. Reusing an already
appended graph cannot recreate earlier knowledge. These are human demo commands;
this guide is excluded from runtime Docker build contexts.

```sh
uv sync --locked
docker compose up -d --wait
uv run ringlens schema init --graph ringlens_memory_demo_20261017
uv run ringlens ingest --input data/demo/base --graph ringlens_memory_demo_20261017
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --graph ringlens_memory_demo_20261017 --format json

# Explicit human observation, scoped only to the current shared device/address.
uv run ringlens reviews record --refund-id ref_4d44e5cc3e0a8dae77fd --reviewer-id demo-reviewer --outcome BENIGN_EXPLANATION --explanation "Shared device and shipping location explained; future refunds are not cleared." --rule R1 --rule R5 --graph ringlens_memory_demo_20261017

# This is a fresh process/session; memory is read from FalkorDB.
uv run ringlens reviews history --refund-id ref_4d44e5cc3e0a8dae77fd --graph ringlens_memory_demo_20261017
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --create-case --graph ringlens_memory_demo_20261017 --format json

uv run ringlens ingest --input data/demo/new_evidence --graph ringlens_memory_demo_20261017
uv run ringlens investigate --refund-id ref_4d44e5cc3e0a8dae77fd --create-case --graph ringlens_memory_demo_20261017 --format json
uv run ringlens reviews history --graph ringlens_memory_demo_20261017
uv run ringlens cases show --case-id inv_b3a91ceaf1c249cbc0ea0da5def7897f46f58b6c166c1de4aee092c9bdf6e954 --graph ringlens_memory_demo_20261017 --format json
```

Observed in disposable real-database tests: the base assessment is **35 LOW**, R1
and R5. Recording the review saves one standalone assessment and two evidence
nodes, with **no Investigation**. Replaying the identical review returns its same
ID/time. A case-policy request returns ALREADY_REVIEWED without a case. After
append, the same group is **100 HIGH**: R1/R5 remain covered, while new R2 bursts,
R3 repetitions, and R4 elevated rates remain unexplained. A case is CREATED;
repetition returns EXISTING_UNCHANGED. The original review is unchanged.

`--rule` is repeatable. `--source-id` narrows selected facts to those containing
one of the supplied source IDs (for example one particular device). All requested
rule/source references must match. Select `--all-evidence` explicitly to consider
all current triggered evidence. A rule may contain several fact units: R2 has a
unit per disjoint burst; R1/R5 per device/address; R4 per qualifying account; R3
covers the repeated episode set. A review of an account in R4 reviews that account's
current transactions, not every future transaction by that account.

Supply `--case-id CASE_ID --case-status CLOSED` to explicitly close an associated
case while recording a review. OPEN, IN_REVIEW, and REOPENED are also explicit
human actions. Case association requires the exact assessment group. Outcomes
alone do not close or confirm a case. `NEEDS_MORE_INFORMATION` and
`CONFIRMED_ABUSE` record consideration but provide no benign clearance.

Corrections use `--supersedes REVIEW_ID` with a changed outcome/explanation and
the **same exact fact scope**. The old node remains; SUPERSEDES points backward.
Only the latest replacement applies, even if that replacement later expires.
A branching correction is rejected. To reproduce old facts after an append, use
`--as-of` and the original window; if the original facts cannot be reproduced,
record a separate newly scoped review rather than rewriting history.
`--valid-until UTC_TIMESTAMP` optionally limits applicability by assessment event
time (inclusive), not by the current wall clock. It must not precede assessment time.

## Matching and material changes

`reviews.evidence_facts()` creates SHA-256 fingerprints from canonical UTF-8 JSON
containing rule identity/version, immutable relevant source records, anchor, and
measurements. Evidence IDs/window/revision are retained separately for audit and
are not fingerprint inputs. A changed assessment ID, import revision, score points,
or clock by itself does not create a new suspicious fact.

A benign scope matches the same rule/version and anchor with the same or a subset
of the reviewed immutable records. Thus merely aging out already explained orders
does not recreate suspicious evidence. A new transaction/account or increased
whole-graph device-user count is not cleared by the old scope. Burst facts include
actual refund/order/account records and measured event times. New qualifying bursts
and changed elevated-rate transaction sets are new facts.

`compare_evidence()` returns active applicable reviews, considered fingerprints,
benign-covered facts, unexplained facts, material new facts, and a recommendation.
It verifies saved review scope against the immutable canonical assessment and
rechecks the current READY dataset/revision. Superseded/expired reviews are excluded
from applicability, but `review_history()` retains them. Legacy four-field Review
nodes remain readable on cases but lack sufficient scope to automate clearance.

"Material new" compares relevant records against the assessed records known at the
applicable reviews, or the latest completed case snapshot when no applicable review
exists. An existing unexplained fact still warrants the ordinary MEDIUM/HIGH policy;
it need not be called new. Other rules are not suppressed by reviewing one signal.

The case writer independently repeats the comparison under the shared writer lease:

| Situation | Policy |
| --- | --- |
| Every current qualifying fact covered by benign review | ALREADY_REVIEWED; preserve case/reviewer status |
| Unexplained COMPLETE MEDIUM/HIGH, no exact case | CREATED, OPEN |
| New complete evidence for existing open case | UPDATED with another immutable assessment |
| CLOSED + material new unexplained evidence + MEDIUM/HIGH | REOPENED on the same case, new assessment and review flag |
| CLOSED + LOW or unchanged evidence | Preserve CLOSED; no automatic reopening |
| New LOW group without coverage | NO_CASE_REQUIRED |
| Incomplete, stale, failed, or unconfirmed assessment | NOT_CREATED_INCOMPLETE |

Overlapping groups still receive distinct case identities and overlap flags; no
membership merge or account-wide exemption is introduced.

## Publication and retry

ScopedReview extends the unchanged legacy Review model with reviewer, dataset,
assessment/evidence references, rule version, fact scope, optional case/status,
supersedes/validity, and publication state. Values and JSON strings are validated;
no nested dictionary is stored as a graph property.

Explicit review writes recompute canonical graph analysis, acquire the existing
renewable writer lease, verify references/current input, save immutable assessment
and evidence if necessary, and stage the review as LOADING. Endpoint/cardinality
checks precede the single final READY publication, including any explicit case
status change. Readers ignore LOADING reviews. Identical intent/assessment/scope
hashes produce the same review ID, so retry neither duplicates nor edits a completed
review. A lost final acknowledgement can require retry to confirm the result.

A reviewed READY assessment can later be attached to a case. HAS_ASSESSMENT now has
its own LOADING/READY publication marker: a staged attachment cannot expose a
completed-but-unpublished case history entry. Legacy links with no marker are
read as published. Final publication sets both markers consistently. Failure/retry
of this path is integration-tested.

The shared lease coordinates review, case, and ingestion writers for this
single-writer MVP. Multi-query writes are not globally atomic, and the lease is
not fencing or a concurrency guarantee. No authentication server exists: the
reviewer identifier is asserted by the local human caller, not authenticated by
an identity provider. The dashboard must keep this trusted-human workflow separate
from agent tools.

```sh
uv run pytest tests/integration/test_reviews.py tests/integration/test_agent.py --run-integration
```
