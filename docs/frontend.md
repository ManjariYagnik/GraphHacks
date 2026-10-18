# Local investigation dashboard

RingLens now has a private, single-operator Streamlit frontend. It calls existing
Python services directly; FalkorDB remains the authoritative graph. No API server
or browser-provided Cypher is used. Start it from the repository root:

```sh
uv sync --locked
docker compose up -d --wait
uv run ringlens doctor
uv run streamlit run src/ringlens/streamlit_app.py --server.address 127.0.0.1
```

Open `http://127.0.0.1:8501`. If `uv` is unavailable on PATH in a particular
shell, `.tools/uv-package/bin/uv` is the repository-local fallback in this
workspace. The app needs the database password in the ignored `.env`, but does not
need an LLM key for upload, insights, scoring, cases or human reviews. Set
`RINGLENS_AGENT_PROVIDER`, the selected provider key, and `RINGLENS_AGENT_MODEL`
only for the optional paid follow-up question. No key is sent to the browser.

The light visual system is defined in `.streamlit/config.toml` using Streamlit's
supported theme settings. `.streamlit/style.css` adds restrained layout and
component treatments. Violet marks primary actions and selected navigation;
teal marks graph activity and positive state. The sidebar always shows the
selected dataset, READY state, and data-through time. Status and priority use
text-labelled badges, so their meaning does not depend on colour alone. The
five overview entity metrics and investigation graph use separate entity colours;
orange/red are reserved for warning/error states. The graph legend and evidence
table remain available alongside the visual path display.

The five sections are:

- **Overview:** graph-verified node counts, 30-day refunded-order percentage,
  in-window refund paise total, UTC activity, cases, and a bounded deterministic
  scan of the first 50 sorted in-window refund IDs. A partial scan reports
  skipped and failed seeds, never silently labels them LOW.
- **Upload Data:** canonical template/sample ZIP downloads, per-file entity and
  header mapping, bounded preview/error report, then an explicit import button.
  Create allocates a separate `ringlens_ui_<uuid>` graph; append uses the selected
  READY graph. Reupload/retry of the same base batch is available for UI-created
  graphs. Uploaded files never contain scenario labels or case data.
- **Investigate:** choose/search a refund, set inclusive `as_of - window_days` to
  `as_of` boundaries, inspect deterministic rule measurements, connected accounts,
  depths, bursts, actual paths and review comparison. The **Investigation agent**
  panel is available before a manual assessment: **Ask agent · read-only** calls
  assess mode; **Run agent and apply case policy** calls investigate mode. The
  latter can create/update/reopen/skip a case only through the backend policy
  tool. A separate deterministic case button remains after **Assess refund**.
  Agent buttons call the configured paid provider only when submitted.
- **Cases & Reviews:** published cases, immutable assessment history, and an
  explicit human review form. LOW assessments can be reviewed without creating a
  case. Review scope is selected by rule and recomputed against FalkorDB; closure
  requires an explicit case association/status choice.
- **Import History:** batch IDs, stages, timestamps and sanitized errors. Non-READY
  datasets block analysis. Retry the original batch after a partial failure.

The selected graph, readiness and data-through timestamp remain visible in the
sidebar. The local registry at `.ringlens/datasets.json` is ignored by Git and
accepts only server-issued graph names plus the configured legacy graph. Selecting
a graph is a local routing choice, not authentication. Do not expose this app to
the public Internet without adding authentication and authorization.

The upload form keeps its data-through value stable across Streamlit reruns. It
resets the default only when the import mode, target graph, or imported horizon
changes. Editing files, mappings, time, or graph after validation still requires
another **Preview and validate** click before import; this is an intentional
pre-write safeguard. If an older running app keeps showing a stale-input warning,
restart Streamlit to load the updated form code.

## Upload-to-insights demo

1. Download **complete synthetic sample**, unzip it, and upload the five CSVs.
   Choose **Create new dataset**, set **Data through** to
   `2026-10-15T10:00:00Z`, then **Preview and validate**. Preview is read-only.
2. Click **Import validated batch**. After the graph verification succeeds,
   select the new graph in the sidebar. **Overview** shows 50 accounts, 250
   orders, 42 refunds, 42 devices and 46 addresses. The 30-day refunded-order
   percentage is 42 / 250 = **16.8%**.
3. Click **Analyze uploaded data** for a deterministic scan. In **Investigate**,
   assess `ref_9082163771ef0a5e5b26`. Pattern B reaches bridge-connected
   accounts at depth two. The graph view uses actual path nodes/edges. Choose a
   path to highlight it, drag to pan, scroll to zoom, and inspect node properties
   and the accessible edge/evidence tables. The score is 90/HIGH on the base
   fixture; this is review priority, not proof.
4. Click **Investigate and apply case policy** to request a case action. The
   service rechecks READY status, input revision and review context; repeat
   actions reuse the same case/assessment. **Cases & Reviews** shows the result.
   To show the complete tool-calling agent in the frontend instead, click
   **Run agent and apply case policy** in the Investigation agent panel. It
   starts from the selected refund, uses the imported data-through time and a
   30-day window, retrieves graph evidence and review context, then calls the
   same policy service. The panel shows run ID/status, sanitized tool activity,
   grounded explanation, and a case action only when the tool confirms it.
   The bound refund ID prevents a tool call for another refund from authorizing
   a case write. Ordinary rerenders do not call the provider again.
5. For the review-memory example, begin instead with household refund
   `ref_4d44e5cc3e0a8dae77fd` on base data (35/LOW). Save a human review of
   rules R1/R5 without creating a case. Download the six-refund append sample,
   unzip and upload its `refunds.csv` with **Data through**
   `2026-10-17T11:00:00Z`. Reassess: new refund bursts raise the priority while
   the earlier review remains in history. The graph result determines the case
   disposition. Never upload `evaluation/demo_ground_truth.json`.

## Import and visualization boundaries

Validation is bound to SHA-256 of uploaded bytes, mappings, ignored headers,
target graph, selected mode/timezone/as-of, server limits and selected graph's
successful import revision. The import service repeats validation immediately
before writes and uses the existing fail-fast single-writer lease. On failure,
metadata remains FAILED/LOADING if connectivity allows; earlier source writes are
not a completed dataset. Replaying identical content is a no-op; conflicting
immutable IDs and second refunds are rejected. No existing graph or named volume
is deleted by the UI.

The visualization is a bounded projection of the investigation service's actual
FalkorDB path records. It displays up to 80 nodes and 120 directed edges and
reports omissions separately from analytical completeness. The ECharts graph
supports pan/zoom and selected-path highlighting; a node-detail selector and
edge/evidence tables provide a text alternative. Address connections support
evidence only and do not drive traversal. The frontend never reads evaluation
labels, creates score rules, or invents graph edges.

The custom assessment time/window controls apply to the deterministic **Assess
refund** form. The agent panel intentionally uses the dataset's current imported
horizon and standard 30-day window; its caption states this before a paid run.
Read-only agent mode saves an AgentRun audit but no case/review mutation. Human
review remains a separate explicit action in **Cases & Reviews**. Provider mock
tests verify the UI/tool workflow; they are not a live Claude smoke test.

For a local smoke check:

```sh
uv run pytest tests/unit/test_csv_adapter.py
uv run pytest tests/integration/test_dashboard.py --run-integration
uv run ruff check .
uv run ruff format --check .
```

Streamlit's official [file uploader](https://docs.streamlit.io/develop/api-reference/widgets/st.file_uploader),
[forms](https://docs.streamlit.io/develop/api-reference/execution-flow/st.form_submit_button),
[ECharts chart](https://docs.streamlit.io/develop/api-reference/charts/st.echarts_chart),
and [AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest)
APIs informed the implementation. AppTest verifies rerenders without writes;
a local health probe verifies the server starts. Manual browser layout and
accessibility inspection remain recommended before recording a demo video.
