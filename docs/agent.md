# Tool-calling investigation agent

One OpenAI Responses adapter uses native structured function calling. The LLM
selects among five typed tools; existing FalkorDB traversals, deterministic scoring,
review comparison, and case policy remain authoritative. Runtime never reads
fixture/evaluation files. This human guide is excluded from runtime build contexts.

## Configure and run

Install the updated lockfile with `uv sync --locked`. In the ignored `.env`, set:

```dotenv
RINGLENS_OPENAI_API_KEY=
RINGLENS_AGENT_MODEL=
RINGLENS_AGENT_TIMEOUT_SECONDS=30
RINGLENS_AGENT_MAX_TURNS=8
RINGLENS_AGENT_MAX_TOOL_CALLS=16
```

Use a real API key and an available Responses/tool-calling model name from your
provider account. There is deliberately no assumed default model. OPENAI_API_KEY
is accepted as a fallback; RINGLENS_OPENAI_API_KEY takes precedence when set.
Never commit or share `.env`. Missing credentials/model fail clearly before a
provider call or graph run is created. SDK failures omit raw provider errors and
keys. Database credentials retain their existing secret handling.

The official SDK is locked at **openai 2.54.0**. Implementation follows the official
[function-calling guide](https://developers.openai.com/api/docs/guides/function-calling)
and [Python API reference](https://developers.openai.com/api/reference/python),
checked on 2026-10-17: strict function schemas, native function_call items and
function_call_output responses, serial tool calls, `store=False`, transport timeout,
and one SDK retry. Encrypted reasoning items needed for stateless Responses
continuation stay in process memory; they are never written to graph audit.
This does not assert any provider retention policy beyond the requested API option.

```sh
docker compose up -d --wait
uv run ringlens schema init
uv run ringlens agent run --message "Explain refund ref_2b42ec18e21a70bdfa15" --mode assess
uv run ringlens agent run --message "Investigate refund ref_9082163771ef0a5e5b26 and open a case if it needs review." --mode investigate --format json
uv run ringlens agent show-run --run-id RUN_ID
```

All commands accept `--graph NAME` for an explicitly selected disposable/demo graph.
Default mode is **assess**. It permits graph analysis, evidence/review lookup, and
case lookup; code rejects apply_case_policy even if the provider requests it.
Assess mode writes AgentRun audit only, and creates no cases, assessments, evidence,
or reviews. Ordinary `ringlens investigate` still writes nothing at all.
**investigate** authorizes policy-controlled case actions without another approval.
The agent cannot record a human review, change numeric scores, issue Cypher, or
supply an authoritative assessment payload.

## Memory demonstration across fresh sessions

Choose an unused graph name (change the name below if it already has appended data).
Each command starts a fresh application process. No previous transcript is needed.

```sh
uv run ringlens schema init --graph ringlens_agent_demo_20261017
uv run ringlens ingest --input data/demo/base --graph ringlens_agent_demo_20261017
uv run ringlens agent run --message "Investigate refund ref_4d44e5cc3e0a8dae77fd and open a case if needed." --mode investigate --graph ringlens_agent_demo_20261017

uv run ringlens reviews record --refund-id ref_4d44e5cc3e0a8dae77fd --reviewer-id demo-reviewer --outcome BENIGN_EXPLANATION --explanation "Shared device and address explained; future refunds are not cleared." --rule R1 --rule R5 --graph ringlens_agent_demo_20261017
uv run ringlens reviews history --refund-id ref_4d44e5cc3e0a8dae77fd --graph ringlens_agent_demo_20261017

uv run ringlens agent run --message "Investigate refund ref_4d44e5cc3e0a8dae77fd and open a case if needed. What was reviewed before?" --mode investigate --graph ringlens_agent_demo_20261017
uv run ringlens ingest --input data/demo/new_evidence --graph ringlens_agent_demo_20261017
uv run ringlens agent run --message "Investigate refund ref_4d44e5cc3e0a8dae77fd and open a case if needed. Explain what changed." --mode investigate --graph ringlens_agent_demo_20261017
uv run ringlens agent run --message "Investigate refund ref_4d44e5cc3e0a8dae77fd and open a case if needed." --mode investigate --graph ringlens_agent_demo_20261017
uv run ringlens agent run --message "Explain case inv_b3a91ceaf1c249cbc0ea0da5def7897f46f58b6c166c1de4aee092c9bdf6e954 and its prior reviews." --mode assess --graph ringlens_agent_demo_20261017
```

Domain services and mocked-native-provider/real-database tests observe:

| Request/data | Score/rules | Confirmed policy action |
| --- | --- | --- |
| Pattern A seed ref_2b42ec18e21a70bdfa15 | 90 HIGH, R1–R4 | CREATED, repeat EXISTING_UNCHANGED |
| Pattern B seed ref_9082163771ef0a5e5b26 | 90 HIGH, R1–R4; distant accounts at depth 2 | CREATED, repeat/another seed reuse |
| Base household before review | 35 LOW, R1/R5 | NO_CASE_REQUIRED |
| Same household after scoped benign review | 35 LOW, R1/R5 | ALREADY_REVIEWED, no case |
| Household after six appended refunds | 100 HIGH, R1–R5 | CREATED; old R1/R5 review survives; R2/R3/R4 new/unexplained |
| Separate controlled CLOSED household case + append | 100 HIGH | REOPENED, same case, earlier review/history retained |

The CLOSED fixture first creates a base MEDIUM case using an explicit test-only
scoring configuration; default household LOW never manufactures a case. See
`test_agent_reopens_closed_case_and_preserves_review` and the corresponding review
integration test. Review notes never affect score. A/B's unchanged facts do not
become suspicious merely because an unrelated household import advances revision.

## Tool/service boundary for the dashboard

`AgentTools(store, mode='assess').execute(name, arguments)` validates strict inputs
and returns `{status, tool, data, errors}`. The five exposed function schemas are
available from `tool_schemas()`:

| Tool | Arguments | Result |
| --- | --- | --- |
| analyze_refund | refund_id, nullable as_of UTC string, nullable window_days | Server-issued assessment_reference, computed identity, complete status, candidate depths/paths, official score/rules, evidence IDs |
| get_evidence | assessment_reference, nullable account_id/rule_id | Actual stable source paths, query IDs, measurements, evidence/source IDs, disclosed omissions/limitations |
| get_review_context | assessment_reference | Applicable human reviews, scoped facts, considered/benign/unexplained/new fingerprints and recommendation |
| apply_case_policy | assessment_reference | Confirmed CaseAction with case/assessment IDs; investigate mode and prior context lookup required |
| get_case | case_id | Published case/current state, completed assessments, legacy and scoped/standalone review history, evidence IDs and omissions |

Application handles are session-local (`ar_...`); a new process must analyze again,
or use a stored READY `asm_...` snapshot. A computed assessment ID alone does not
imply persistence. Saved snapshots are validated by the original case services;
the model cannot substitute a score or arbitrary JSON assessment. Memory comparison
and every case write recheck Dataset READY/input revision. Historical case lookup
remains available even when a fresh assessment would be stale.

Other reusable interfaces: `investigate`, `compare_evidence`, `record_review` with
ReviewRequest (trusted human workflow only), `review_history`, `apply_case_policy`,
`get_case`, `list_cases`, `run_agent`, and `get_agent_run`. The dashboard can call
these directly without subprocesses. Review history includes superseded decisions;
applicable comparison excludes them.

## Bounds, grounding, and run history

Default graph investigation caps and query deadlines are unchanged. Tool results
are bounded to 60 KB; evidence paths display at most 20 with an omission count.
Oversized results explicitly request a narrower filter rather than claiming full
display. Display omissions never recompute scores. Runs allow 8 provider turns,
16 tool calls, 30-second requests, one transport retry, and a total deadline.
Settings cap turns at 16, calls at 32, timeout at 60 seconds. Call IDs are cached
within the run; inconsistent reuse fails. Unknown arguments/tools/references,
missing data, stale revision, incomplete discovery, and loop/provider errors return
explicit failures and nonzero CLI outcomes. Incomplete analysis cannot create a
normal LOW case result. Exit 1 means unavailable/configuration failure; exit 3
means a run did not complete its required tool work.

Final text is rendered by deterministic code from confirmed tool/domain records,
not the model's free-form prose. It names actual source/evidence/review/case IDs,
connections, contributions, reviewed/unexplained/new facts, and confirmed actions.
Unsupported model claims are discarded. Review notes and source labels are marked
untrusted data in provider instructions. The tool boundary enforces write scope,
reference validation, and policy independently of those instructions. The priority
heuristic remains separate from proof of wrongdoing.

AgentRun persists run ID/mode, requested entity IDs obtained from typed calls,
computed assessment IDs/session references, case references, sanitized tool activity,
status, timestamps, errors, and grounded final text. Raw prompts, tool arguments,
provider reasoning, credentials, and unnecessary payloads are not stored. Read it
with `agent show-run`. RUNNING left by a crash is not success; an interrupted or
uncertain run must inspect confirmed case state before retrying. Case writes remain
idempotent. Audit failure after a case write explicitly reports uncertainty rather
than pretending the action never happened. This is a single-writer demo, with no
API authentication, background workers, or concurrency guarantee.

## Actual verification and live smoke

Offline SDK HTTP tests use a fake transport; orchestration tests use deterministic
ProviderTurn mocks while graph tools execute against real disposable FalkorDB.
They are **not live provider verification**. This workspace has no configured API
key or model, so a live request was unavailable. The adapter and CLI are implemented;
provider/model/account compatibility must be confirmed after configuration:

```sh
uv run pytest tests/unit/test_agent_provider.py
uv run pytest tests/integration/test_reviews.py tests/integration/test_agent.py --run-integration
uv run pytest tests/integration/test_agent_live.py --run-integration --run-live-agent
```

The last command performs one live read-only smoke only with credentials/model
present; otherwise it explicitly skips. It may incur provider charges and cleans
up only its own uniquely named test graph. The application graph/volume are never
reset by these tests. See implementation-status for final actual test totals.
