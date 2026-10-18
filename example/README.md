# Five reproducible RingLens workflows

These examples use synthetic subsets of the checked-in seed-42 fixture. They
demonstrate the current application: strict source validation, idempotent
FalkorDB ingestion, graph-led discovery, deterministic v1 scoring, case policy,
and optional human review memory. The runner does **not** read
`evaluation/demo_ground_truth.json` or ask an LLM to assign scores. All five
examples were exercised against disposable FalkorDB graphs in integration tests.

| Example | What it demonstrates | Verified result |
| --- | --- | --- |
| [01 · ordinary refund](example_01/workflow.md) | Isolated order/refund path and low-risk control | 1 candidate; 0/LOW; no case |
| [02 · shared resources](example_02/workflow.md) | Shared device/address without synchronized refunds | 3 candidates; 35/LOW; no case |
| [03 · one-device cluster](example_03/workflow.md) | Bursts and elevated rate across one shared device | 4 candidates; 90/HIGH; case created |
| [04 · two-device bridge](example_04/workflow.md) | FalkorDB's second expansion reaches distant accounts | 3 after first expansion, 5 total; 90/HIGH; case created |
| [05 · new evidence](example_05/workflow.md) | Explicit scoped review, refund-only append, changed assessment | 35/LOW → 100/HIGH; new case |

Each `data.json` is a **demo envelope**, not an operational manifest. It contains
`schema_version`, deterministic `seed`, `seed_refund_id`, and `base`/optional
`append` segments. Each segment contains `as_of` and the five canonical record
arrays. Source rows use exactly the fields in [the data contract](../docs/data-contract.md):
strict type-prefixed IDs, integer UTC seconds, positive INR minor units, and
invented address labels. There are no scenario flags or scores in source rows.
`example/demo/demo.py` validates each segment, calls the existing
`dataset_io.make_batch()` to derive canonical five-file manifest metadata, and
passes that `DatasetBatch` to the existing importer. The standalone JSON is never
stored as a graph property. The normal CSV uploader remains available for other
datasets; these demos do not add a second ingestion architecture.

## Prerequisites and commands

From the repository root, install dependencies and configure the ignored `.env`
as described in the [main README](../README.md). `RINGLENS_FALKORDB_HOST`,
`RINGLENS_FALKORDB_PORT`, and optional `RINGLENS_FALKORDB_PASSWORD` are read by
the existing `Settings`; the runner does not print credentials. Start FalkorDB:

```sh
uv sync --locked
docker compose up -d --wait
uv run ringlens doctor
```

If `uv` is not on PATH but dependencies are already installed, use
`.venv/bin/python` and `.venv/bin/ringlens` in the commands below. The default
demo is example 04. A dry-run requires neither FalkorDB nor an LLM key:

```sh
uv run python example/demo/demo.py --example 04 --dry-run
uv run python example/demo/demo.py --example 04
```

Select `01`, `02`, `03`, `04`, or `05` with `--example`. For an explicit human
review before example 05's append, supply all three review flags:

```sh
uv run python example/demo/demo.py --example 05 \
  --reviewer-id demo-reviewer \
  --review-outcome BENIGN_EXPLANATION \
  --review-explanation "Shared device and address explained; later refunds are not cleared."
```

The outcome is **human input** to the review service; the runner does not infer
or invent it. The `--with-agent` option separately makes an opt-in paid,
read-only native tool-calling request after deterministic graph/case verification.
Configure `RINGLENS_AGENT_PROVIDER`, its matching API key, and
`RINGLENS_AGENT_MODEL` in `.env` first. The optional agent may fail due to key,
model, credit, network, or tool-loop limits; the runner reports that as incomplete
rather than claiming a live agent success. No provider call occurs by default.

## Persistence and UI inspection

Every live run allocates a fresh `ringlens_example_<random>` graph, refuses to
adopt any existing graph, and leaves its graph in FalkorDB for inspection. Only
the tests delete their own uniquely named test graphs. The runner verifies the
import's READY metadata and exact source counts, reads the seed refund's account,
order, device and address from FalkorDB, checks case publication and retry
behavior, then opens a **new database connection** and reads affected records
again. That proves persistence across application connections; it does not claim
durability across a server restart. `docker compose down` retains the named
volume; do not use `down -v` for this demo.

After a live run, use the graph name and case ID printed by the runner:

```sh
uv run ringlens graph verify --graph GRAPH_PRINTED_BY_RUNNER
uv run ringlens investigate --refund-id ref_9082163771ef0a5e5b26 --graph GRAPH_PRINTED_BY_RUNNER
uv run ringlens cases show --case-id CASE_ID_PRINTED_BY_RUNNER --graph GRAPH_PRINTED_BY_RUNNER
RINGLENS_GRAPH_NAME=GRAPH_PRINTED_BY_RUNNER uv run streamlit run src/ringlens/streamlit_app.py --server.address 127.0.0.1
```

Use the matching seed from each workflow when running a different example.
Restart an already running Streamlit process with `RINGLENS_GRAPH_NAME` set so
the disposable graph appears as its configured selection. In **Investigate**,
the graph uses short labels; the expanded ID key maps them to the actual IDs.
The frontend is local/private and has no authentication for public hosting.

## Why example 04 wins

The [Graph Hacks rules](https://www.wemakedevs.org/hackathons/falkordb/rules)
require FalkorDB to be central, rather than a decorative graph view. They name
the *Agents That Act on Connected Data* and *Agent Memory and Coordination*
tracks and require a public repository, graph model, relevant Cypher, working
video, and deployment or complete local setup. The table below is our
presentation assessment, **not an official judging rubric**.

| Criterion | 01 control | 02 shared resources | 03 one device | 04 bridge | 05 new evidence |
| --- | --- | --- | --- | --- | --- |
| Theme relevance | Valid negative control | Shows careful judgment | Connected-data detection | **Graph traversal is indispensable** | Persistent human memory |
| Narrative clarity | Very simple | Clear false-positive contrast | Clear cluster | **Surprise: peers appear at hop two** | Before/after story needs time |
| Core capability | Import + read + score | Traversal + mitigation | Traversal + rules + case | **Two traversals + rules + case** | Import + review + reassessment |
| Differentiation | Baseline only | Explains non-escalation | Shared-device pattern | **Bridge cannot be seen from seed device alone** | Scoped review memory |
| Measurable result | 1 account, 0/LOW | 3 accounts, 35/LOW | 4 accounts, 90/HIGH | **3 → 5 accounts, 90/HIGH** | 35 → 100, 3 → 9 refunds |
| Live reliability | Very high | High | High | **High; one base batch** | More actions and timing |
| Visual clarity | Sparse path | Simple shared path | Dense star | **Distinct two-device bridge path** | Before/after views |
| Database persistence | Source read-back | Source read-back | Source + case read-back | **Source + case + repeat read-back** | Source + review + case read-back |

Example 04 provides the strongest 4–5 minute technical story: the seed account
reaches only three members after one device expansion, then a bridge's other
device reaches two more. The stored path, deterministic score, case write, and
read-back are all visible. Example 05 is a strong follow-up if more time is
available to show reviewer memory. None of the example scores estimates actual
fraud probability or real-world detection accuracy.

## Verification and limits

```sh
uv run pytest tests/unit/test_example_data.py
uv run pytest tests/integration/test_example_workflows.py --run-integration
uv run ruff check .
uv run ruff format --check .
```

The unit tests validate all source fields, references, timestamps, and canonical
manifest files, and prove dry-run does not connect to FalkorDB. Integration
tests run all five workflows on real disposable graphs and delete only those
test graphs. [Expected output](demo/expected_output.md) distinguishes measured
graph results from variable graph IDs and optional provider behavior. The
[presentation script](demo/demo_script.md) gives the timed live sequence.

Before submission, verify the public repository in a private browser window,
record the actual working demo, name the selected track, and disclose AI coding
assistance as the rules require. A simulated dry-run or mocked provider test
must never be presented as live graph/agent execution.
