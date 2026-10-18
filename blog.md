# RingLens: following the connections behind a refund

## 1. The refund row looks ordinary

A reviewer opens a refund request: one account, one order, one amount. Nothing in that row says “coordinated activity.” The useful question is harder: who else is connected to this customer, and are their refunds following the same pattern?

RingLens is my Graph Hacks submission for **Agents That Act on Connected Data** and **Agent Memory and Coordination**. It investigates refunds through FalkorDB, calculates an explainable review priority, and gives a tool-calling agent access to the same services as the dashboard and CLI.

Start with one account. Device sharing reveals three accounts after one expansion and five after two. All five requested synchronized refunds on three separate dates. The resulting priority is **90/HIGH**. The signal lives in the connections and their timing.

## 2. The Graph Model

The source graph has five node types: `Account`, `Order`, `Device`, `Address`, and `Refund`. An account `PLACED` an order; that order `USED_DEVICE` and `SHIPPED_TO` an address. An account `REQUESTED_REFUND`, and the refund is `FOR_ORDER`.

Device use belongs to an order, giving each use a timestamp through `placed_at`. Accounts do not have a direct device edge that loses this context. Orders, refunds, and ownership references are validated before import using strict Pydantic v2 models and cross-record checks.

Relationships are the investigation data, not a secondary index over a precomputed account list. Runtime discovery reads actual FalkorDB edges through parameterized Cypher. Python 3.12 services validate those results, calculate priority, and support both Streamlit and the Typer CLI.

## 3. How Discovery Works

Imagine accounts A and B using device D1. Account C uses D1 on one order and D2 on another. Accounts D and E use D2.

Starting from A’s refund, RingLens resolves the owning account. The first expansion follows A’s orders to their devices, then traverses back through other orders to their owners. D1 reveals B and C: **one seed becomes three candidates**.

The second expansion starts from newly discovered accounts. C’s D2 order exposes D and E: **three candidates become five**. These are account expansion hops; each contains several graph edges.

The fixed Cypher pattern filters both orders to the observation window, defaults to 30 days, excludes visited accounts, and returns distinct peers. Separate witness queries preserve paths so the reviewer can trace the bridge back to A.

A flat refund row cannot reveal this. A relational implementation could reconstruct it with joins, but it would need to express the same traversal. RingLens makes FalkorDB essential: removing it prevents discovery. Database failures or exceeded discovery bounds produce an explicit incomplete result without a score, rather than a misleading LOW.

## 4. Transparent Scoring

Once discovery completes, a deterministic Python engine evaluates five rules. Default contributions total at most 100:

| Rule | Trigger | Points |
| --- | --- | ---: |
| R1 | A device has at least three candidate users and at most five users across the graph within the window | 25 |
| R2 | At least three accounts request refunds within a rolling 30-minute interval | 25 |
| R3 | Qualifying bursts occur on at least two distinct UTC start dates | 25 |
| R4 | At least three accounts each placed five or more orders and refunded at least half | 15 |
| R5 | At least three candidate accounts share a shipping address | 10 |

Each rule contributes once. LOW covers 0–39, MEDIUM 40–69, and HIGH 70–100. Counts use distinct records, so multiple paths cannot inflate a score.

In the bridge example, each account refunded three of five orders, with synchronized bursts on three dates. R1–R4 contribute 25 + 25 + 25 + 15 = **90**; R5 contributes zero.

This is review priority, not a calibrated fraud probability. A reviewer needs the triggering records, thresholds, and mitigating context. Shared devices can have ordinary explanations; an opaque probability would not resolve that ambiguity.

## 5. The Agent’s Role

The agent uses the official Anthropic or OpenAI SDK with structured tool calling. Its tools analyze refunds, retrieve evidence, retrieve review context, inspect cases, and apply case policy.

Those calls reach the same validated services used by Streamlit and the CLI. The model cannot write arbitrary Cypher, submit a numeric score, substitute its own assessment, or record a human review outcome. Final responses are rendered from confirmed tool results, excluding unsupported model claims.

Assess mode permits analysis and lookup. Investigate mode authorizes policy-controlled case actions. Before applying policy, the service requires review context and rechecks completeness and the dataset revision. Repeated equivalent requests reuse existing case state.

## 6. Evidence-Scoped Memory

A useful investigation assistant must remember what a human explained without granting permanent immunity.

RingLens stores human `Review` nodes in FalkorDB, linked to assessments and selected evidence. Scope includes rule identity/version and fingerprints of relevant immutable facts. Fresh agent sessions retrieve this memory from the graph.

The household example starts at **35/LOW**, triggering R1 and R5. A human can explain the shared device and address. That review leaves the score unchanged and covers those facts only.

Later, six appended refunds introduce synchronized bursts, repetition, and elevated rates. The group reaches **100/HIGH**. The earlier R1/R5 explanation survives, while R2–R4 remain unexplained and can justify a case. A previously closed case can reopen only under the policy’s conditions for material new, unexplained qualifying evidence.

## 7. What I Learned / What’s Next

The hardest design problem was deciding when a past explanation still applies. Evidence scope makes that decision inspectable instead of depending on a remembered conversation.

This is an MVP with synthetic data only, no multi-user authentication, and no calibrated fraud accuracy. Its shared writer lease and publication markers do not provide a production concurrency guarantee.

Next steps are authenticated reviewer identities, stronger concurrent-write guarantees, evaluation on appropriately governed real data, and measured tuning of thresholds and discovery limits. The principle I would keep is simple: every priority and case action should remain traceable to graph evidence and explicit policy.
