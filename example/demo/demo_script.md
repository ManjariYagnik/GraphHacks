# Graph Hacks live demo · two-device bridge (about 4–5 minutes)

Use [example 04](../example_04/workflow.md). Rehearse with the fixed seed refund
`ref_9082163771ef0a5e5b26`. Before recording, run `uv sync --locked`, configure
the ignored `.env`, start Docker with `docker compose up -d --wait`, and check
`uv run ringlens doctor`. Keep one terminal for the runner, one for Streamlit,
and a browser at `http://127.0.0.1:8501`. The app is local/private. If you want
to show the **real tool-calling agent** during the video, configure your Claude
or OpenAI provider/model/key first and budget for one paid request; otherwise
say plainly that this run demonstrates the deterministic graph/case services.

| Time | Presenter narration | Show and perform | Expected output and point for judges |
| --- | --- | --- | --- |
| 0:00–0:35 · Hook | “A refund on its own looks routine. The interesting part is who used the same device, and who connects through someone else's second device. A flat refund list hides that bridge.” | Show README graph diagram and the seed refund ID in example 04. | Account → Order → Device ← Order ← Account is the actual stored path, not a slide-only drawing. |
| 0:35–1:00 · Input | “These are invented accounts, INR amounts in paise, and integer UTC event times. The example is validated before anything is written.” | In terminal: `uv run python example/demo/demo.py --example 04 --dry-run` | 5 accounts, 2 devices, 25 orders, 15 refunds; `DRY RUN ONLY` means no database operation occurred. |
| 1:00–2:05 · Live import and traversal | “Now I create a new isolated FalkorDB graph. RingLens writes the source records and verifies every required edge. It starts at this refund, follows its order's device, then expands again through a bridge account.” | Run `uv run python example/demo/demo.py --example 04`. Point to printed graph name, READY batch, refund read-back, and the `Two-expansion path`. | First expansion reaches 3 accounts; the second reaches 5. The path printed through both device IDs came from FalkorDB queries. |
| 2:05–2:50 · Score and case | “The score is an explicit review-priority heuristic: shared device 25, synchronized refunds 25, repeated bursts 25, and elevated refund rates 15. That's 90/HIGH, not a probability of fraud.” | Point to `rules=R1+25, R2+25, R3+25, R4+15`, `Case policy: CREATED`, then `repeat is unchanged`. | One published OPEN case; the idempotent retry did not create another. The runner read it back with score and membership. |
| 2:50–3:40 · Graph on screen | “This is the exact bridge path. Short labels keep the graph readable, and the ID key shows the source IDs behind them.” | Run `export DEMO_GRAPH=GRAPH_PRINTED_BY_RUNNER` with the actual value. In another terminal run `RINGLENS_GRAPH_NAME="$DEMO_GRAPH" uv run streamlit run src/ringlens/streamlit_app.py --server.address 127.0.0.1`. In **Investigate**, select the seed refund, click **Assess refund**, highlight a depth-two evidence path, open the ID key. | Five candidates; two at depth two; real directed edges and full IDs in the key. The view is a projection of query results, not a replacement graph. |
| 3:40–4:20 · Persistence and optional agent | “The case is stored in FalkorDB. A fresh process can read it back. If provider access is enabled, the agent can ask typed tools for this assessment and review context; scoring and case policy remain in code.” | Run `uv run ringlens graph verify --graph "$DEMO_GRAPH"`, then `uv run ringlens cases show --case-id inv_424afacab8e9c9bbf84f504eef395356c83203d5db6d22d30550676c2e1aa670 --graph "$DEMO_GRAPH"`. If credentials work, click **Ask agent · read-only** in Streamlit and show its confirmed tool activity/AgentRun status. | READY graph, OPEN case, completed assessment. A live agent result is valid only if the UI reports COMPLETE; its tool calls are optional and paid. |
| 4:20–4:45 · Close | “RingLens finds relationships that a single refund row cannot show, scores the evidence transparently, and preserves an investigation that a reviewer can revisit. The graph drives the decision path.” | Return to the graph and case ID. | Measured fixture result: 3 → 5 discovered accounts and one read-back case. No claim of real-world fraud accuracy. |

If the live provider returns INCOMPLETE or cannot run, do not call it a successful
agent demo. The deterministic import, traversal, scoring, case write, and
read-back remain independently demonstrable. If the database is unavailable,
show the dry-run validation only and state that graph behavior/persistence was
not exercised. `example/demo/demo.py` exits nonzero on these failures.

For judging context, the [Graph Hacks rules](https://www.wemakedevs.org/hackathons/falkordb/rules)
require a public repository, graph model and queries, a working video, local
setup or deployment, named track, and AI coding-assistance disclosure. The
natural track here is *Agents That Act on Connected Data*; example 05 also
demonstrates *Agent Memory and Coordination*. Verify the public repository link
and record what actually ran before submission.
