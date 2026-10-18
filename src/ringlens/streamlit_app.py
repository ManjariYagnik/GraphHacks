"""Local single-operator Streamlit dashboard; all domain work stays in services."""

from datetime import UTC, datetime
from html import escape
from pathlib import Path

import streamlit as st

from ringlens import cases
from ringlens.agent import run_agent
from ringlens.agent_provider import AgentSettings, ProviderError, create_provider
from ringlens.csv_adapter import (
    CSVLimits,
    CSVSource,
    csv_bundle,
    csv_headers,
    error_report,
    required_columns,
    suggested_kind,
    suggested_mapping,
    validate_csv_sources,
)
from ringlens.dashboard_data import (
    MAX_SCAN_SEEDS,
    available_graphs,
    dataset_status,
    import_validated_csv,
    new_graph_name,
    overview,
    refund_ids,
    scan_refunds,
    selected_store,
)
from ringlens.graph_store import GraphStore, GraphStoreError
from ringlens.graph_view import echarts_spec, graph_projection
from ringlens.investigation_models import InvestigationConfig
from ringlens.investigator import investigate
from ringlens.review_models import ReviewRequest
from ringlens.reviews import compare_evidence, record_review, review_history
from ringlens.synthetic import parse_as_of
from ringlens.verify import stable_path

STYLE_PATH = Path(__file__).resolve().parents[2] / ".streamlit" / "style.css"
NAV_ICONS = {
    "Overview": "◫",
    "Upload Data": "↥",
    "Investigate": "⌕",
    "Cases & Reviews": "▣",
    "Import History": "◷",
}


def _page_heading(title: str, description: str) -> None:
    st.markdown(
        '<div class="rl-page-head"><div class="rl-page-kicker">Investigation desk</div>'
        f"<h1>{escape(title)}</h1><p>{escape(description)}</p></div>",
        unsafe_allow_html=True,
    )


def _chip(label: str, tone: str) -> str:
    allowed = {"ready", "pending", "warning", "error", "low", "medium", "high"}
    safe_tone = tone if tone in allowed else "pending"
    return f'<span class="rl-badge {safe_tone}">● {escape(label)}</span>'


def _metric_card(column, label: str, value: str | int, icon: str, tone: str, **kwargs):
    with column.container(border=True):
        st.markdown(
            f'<span class="rl-metric-icon {tone}" aria-hidden="true">{escape(icon)}</span>',
            unsafe_allow_html=True,
        )
        st.metric(label, value, **kwargs)


def _upload_steps(active: int, slot=None) -> None:
    steps = ("Upload", "Validate", "Import", "View insights")
    pieces = []
    for index, label in enumerate(steps):
        state = "done" if index < active else "active" if index == active else ""
        pieces.append(f'<span class="rl-step {state}">{index + 1}. {label}</span>')
    content = "".join(pieces)
    target = slot if slot is not None else st
    target.markdown(f'<div class="rl-steps">{content}</div>', unsafe_allow_html=True)


def utc(value):
    return (
        datetime.fromtimestamp(value, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        if value is not None
        else "—"
    )


def _clear_cached_work():
    for key in (
        "csv_validation",
        "assessment",
        "assessment_key",
        "scan",
        "scan_key",
        "agent_result",
        "case_action",
    ):
        st.session_state.pop(key, None)


def _service_error(error):
    # Domain exceptions are intentionally sanitized. Never render provider/driver internals.
    if isinstance(error, (ValueError, ProviderError, GraphStoreError)):
        return str(error)
    return "Operation failed; inspect database availability and retry with the same input."


def _badge(status):
    if status == "READY":
        st.success("READY · assessments available", icon="✅")
    elif status == "EMPTY":
        st.info("No imported dataset in this graph yet.")
    else:
        st.warning(f"{status} · complete or retry the unfinished import before analysis.")


def _overview(store, state):
    _page_heading(
        "Overview", "A clear view of your graph, refund activity, and investigation work."
    )
    st.caption("Synthetic demo · FalkorDB source of truth · inclusive 30-day window")
    data = overview(store)
    if not data["ready"]:
        _badge(state["status"])
        if data.get("issues"):
            st.error("Graph verification failed: " + "; ".join(data["issues"]))
        st.info("Upload a validated base dataset to see graph-derived insights.")
        return
    counts = data["counts"]
    top = st.columns(5, gap="medium")
    for column, kind, icon, tone in zip(
        top,
        ("accounts", "orders", "refunds", "devices", "addresses"),
        ("◎", "▤", "↩", "◇", "⌂"),
        ("teal", "blue", "violet", "indigo", "slate"),
        strict=True,
    ):
        _metric_card(column, kind.title(), counts[kind], icon, tone)
    left, right = st.columns(2, gap="medium")
    _metric_card(
        left,
        "Refunded orders / in-window orders",
        f"{data['refunded_order_percent']:.2f}%",
        "◉",
        "teal",
        help=f"{data['refunded_orders']} refunded / {data['order_count']} window orders",
    )
    _metric_card(
        right,
        "In-window refund amount",
        f"₹{data['refund_amount_minor'] / 100:,.2f}",
        "₹",
        "violet",
    )
    st.caption(
        f"Window {utc(data['window_start'])} through {utc(data['window_end'])}, inclusive. "
        f"Last import: {utc(data['last_import']['completed_at']) if data['last_import'] else '—'}"
    )
    if data["refund_activity"]:
        st.subheader("Refund requests by UTC date")
        st.bar_chart(
            data["refund_activity"],
            x="date",
            y="refunds",
            color="#0F766E",
            height=290,
            x_label="UTC date",
            y_label="Refund requests",
            alt="Daily refund request counts within the selected inclusive assessment window",
        )
    else:
        st.info("No in-window refunds yet. Upload refunds to enable investigations.")
    st.subheader("Cases")
    if data["cases"]:
        st.dataframe(data["case_counts"], hide_index=True)
        if data["case_list_truncated"]:
            st.warning("Case counts show only the first 100 published cases.")
        st.dataframe(
            [
                {
                    "case_id": c["investigation"]["id"],
                    "status": c["investigation"]["status"],
                    "priority": c["investigation"]["priority"],
                    "score": c["investigation"]["score"],
                }
                for c in data["cases"]
            ],
            hide_index=True,
        )
    else:
        st.caption("No cases have been created.")
    st.subheader("Analyze uploaded data")
    st.caption(
        f"Scans up to {MAX_SCAN_SEEDS} in-window refund IDs in sorted order. "
        "Deterministic graph investigations only; no LLM calls or case writes."
    )
    with st.form("scan_form"):
        days = st.number_input("Window days", min_value=1, max_value=3650, value=30)
        start_scan = st.form_submit_button("Analyze uploaded data", type="primary")
    key = (store._graph.name, state["revision"], state["as_of"], int(days), "v1")
    if start_scan:
        progress = st.progress(0, text="Investigating graph refunds")
        try:
            result = scan_refunds(
                store,
                as_of=state["as_of"],
                window_days=int(days),
                progress=lambda done, total: progress.progress(
                    done / max(1, total), text=f"Assessed {done}/{total}"
                ),
            )
            st.session_state.scan_key, st.session_state.scan = key, result
        except Exception as error:
            st.error(_service_error(error))
    if st.session_state.get("scan_key") == key:
        result = st.session_state.scan
        st.write(
            f"Coverage: {result['assessed']}/{result['available']} refund seeds; "
            f"skipped {result['skipped']}; failed {len(result['failed'])}. "
            f"Status: {'COMPLETE' if result['complete'] else 'PARTIAL'}."
        )
        if result["findings"]:
            st.dataframe(result["findings"], hide_index=True)
        if result["failed"]:
            st.warning(
                "Some seeds were unavailable or incomplete; they were not assigned LOW priority."
            )
            st.dataframe(result["failed"], hide_index=True)


def _upload(store, graph, state):
    _page_heading("Upload Data", "Bring your records into FalkorDB in a few careful steps.")
    st.markdown(
        '<div class="rl-upload-intro"><div class="rl-upload-icon" aria-hidden="true">↥</div>'
        "<div><strong>Start with entity CSV files</strong><p>Upload accounts, devices, addresses, "
        "orders, and refunds together for a new dataset. Preview and validation never write data."
        "</p></div></div>",
        unsafe_allow_html=True,
    )
    limits = CSVLimits()
    st.caption(
        f"Limits: {limits.max_file_rows:,} rows and {limits.max_file_bytes:,} bytes/file; "
        f"{limits.max_total_rows:,} rows and {limits.max_total_bytes:,} bytes/batch."
    )
    first, second, third = st.columns(3)
    first.download_button(
        "Download empty CSV templates",
        csv_bundle(),
        "ringlens_csv_templates.zip",
        "application/zip",
    )
    second.download_button(
        "Download complete synthetic sample",
        csv_bundle(sample=True),
        "ringlens_synthetic_sample.zip",
        "application/zip",
    )
    third.download_button(
        "Download six-refund append sample",
        csv_bundle(sample=True, append=True),
        "ringlens_synthetic_append.zip",
        "application/zip",
    )
    st.info(
        "IDs keep their acct_/dev_/addr_/ord_/ref_ prefixes and leading zeros. "
        "Timestamps: UTC Unix seconds or ISO 8601 with an offset. "
        "INR amounts are integer paise: 12345 = INR 123.45. "
        "Address labels must be invented placeholders such as 'Synthetic address A01'."
    )
    create = st.radio(
        "Import type",
        ["Create new dataset", "Append to selected dataset", "Retry/reupload selected base"],
        horizontal=True,
    )
    mode = "append" if create == "Append to selected dataset" else "base"
    if create == "Create new dataset":
        if "new_graph" not in st.session_state:
            st.session_state.new_graph = new_graph_name()
        target = st.session_state.new_graph
        revision = ""
        st.caption(f"A separate graph will be allocated: {target}")
    else:
        target, revision = graph, state["revision"]
        if create == "Retry/reupload selected base" and not graph.startswith("ringlens_ui_"):
            st.warning("Only UI-created graphs can replay their original base CSV batch.")
            return
        if mode == "append" and state["status"] != "READY":
            st.warning(
                "Append needs a READY graph. Retry failed base imports with the original files."
            )
            return
    default_as_of = (
        state["as_of"] + 86400
        if mode == "append" and state["as_of"]
        else state["as_of"]
        if create == "Retry/reupload selected base" and state["as_of"]
        else int(datetime.now(UTC).timestamp())
    )
    # A clock-derived `value=` changes on rerun. For an unkeyed text input,
    # Streamlit then creates a new widget and silently changes the validated
    # fingerprint. Initialize once for this import target instead.
    input_context = (create, target, state["as_of"])
    if st.session_state.get("upload_as_of_context") != input_context:
        st.session_state.upload_as_of_context = input_context
        st.session_state.upload_as_of = utc(default_as_of).replace(" UTC", "Z").replace(" ", "T")
        st.session_state.pop("csv_validation", None)
    as_of_text = st.text_input(
        "Data through (UTC)",
        key="upload_as_of",
        persist_state="session",
        help="Whole-second UTC timestamp; this is the batch's assessment horizon.",
    )
    source_timezone = (
        st.text_input(
            "Source timezone for timestamps without an offset (optional)",
            help="IANA name, e.g. Asia/Kolkata. Ambiguous local times require an explicit offset.",
        )
        or None
    )
    steps_slot = st.empty()
    uploads = st.file_uploader(
        "Entity CSV files",
        type=["csv"],
        accept_multiple_files=True,
        max_upload_size=max(1, (limits.max_file_bytes + 999_999) // 1_000_000),
    )
    sources = []
    if uploads:
        for index, uploaded in enumerate(uploads):
            content = uploaded.getvalue()
            with st.expander(f"{uploaded.name} · map columns", expanded=True):
                kinds = ["Select entity", "accounts", "devices", "addresses", "orders", "refunds"]
                suggested = suggested_kind(uploaded.name)
                kind = st.selectbox(
                    "Entity type",
                    kinds,
                    index=kinds.index(suggested) if suggested else 0,
                    key=f"kind_{index}_{uploaded.name}",
                )
                if kind == "Select entity":
                    st.warning("Choose the entity type for this file.")
                    continue
                try:
                    headers = csv_headers(content)
                except ValueError as error:
                    st.error(str(error))
                    continue
                suggestions = suggested_mapping(headers, kind)
                mapping = {}
                for column in required_columns(kind):
                    options = ["Unmapped", *headers]
                    choice = st.selectbox(
                        column,
                        options,
                        index=options.index(suggestions[column]) if column in suggestions else 0,
                        key=f"map_{index}_{uploaded.name}_{kind}_{column}",
                    )
                    if choice != "Unmapped":
                        mapping[column] = choice
                ignored = st.multiselect(
                    "Explicitly ignore extra columns",
                    headers,
                    key=f"ignore_{index}_{uploaded.name}_{kind}",
                )
                sources.append(CSVSource(uploaded.name, kind, content, mapping, tuple(ignored)))
    try:
        as_of = parse_as_of(as_of_text)
    except ValueError:
        as_of = None
        if uploads:
            st.error("Data-through time must be whole-second UTC, e.g. 2026-10-15T10:00:00Z.")
    current_validation = st.session_state.get("csv_validation")
    valid_current = False
    if current_validation and as_of is not None:
        from ringlens.csv_adapter import input_fingerprint

        valid_current = current_validation.valid and current_validation.fingerprint == (
            input_fingerprint(sources, mode, as_of, target, revision, source_timezone)
        )
    _upload_steps(2 if valid_current else 1 if sources else 0, steps_slot)
    if st.button("Preview and validate", type="primary", disabled=not sources or as_of is None):
        try:
            if mode == "base":
                from ringlens.config import Settings

                check_store = GraphStore(Settings(graph_name=target))
            else:
                check_store = store
            try:
                validation = validate_csv_sources(
                    sources,
                    mode=mode,
                    as_of=as_of,
                    store=check_store,
                    graph=target,
                    revision=revision,
                    source_timezone=source_timezone,
                )
            finally:
                if mode == "base":
                    check_store.close()
            st.session_state.csv_validation = validation
            _upload_steps(2 if validation.valid else 1, steps_slot)
        except Exception as error:
            st.error(_service_error(error))
    validation = st.session_state.get("csv_validation")
    if validation:
        from ringlens.csv_adapter import input_fingerprint

        current = input_fingerprint(sources, mode, as_of, target, revision, source_timezone)
        if current != validation.fingerprint:
            st.warning(
                "Inputs or settings changed since validation (files, mappings, data-through "
                "time, or graph). Click Preview and validate again before importing."
            )
            return
        for preview in validation.previews:
            st.subheader(f"{preview.filename} · {preview.count} valid rows")
            if preview.rows:
                st.dataframe(preview.rows, hide_index=True)
        if validation.issues:
            blocking = sum(issue.severity == "ERROR" for issue in validation.issues)
            st.markdown(
                _chip(f"{blocking} blocking errors", "error" if blocking else "warning"),
                unsafe_allow_html=True,
            )
            st.dataframe([issue.as_dict() for issue in validation.issues], hide_index=True)
            st.download_button(
                "Download validation report",
                error_report(validation.issues),
                "ringlens_validation_errors.csv",
                "text/csv",
            )
        if not validation.valid:
            st.error("Blocking validation errors; no import is available.")
            return
        st.success(
            f"Validated {sum(p.count for p in validation.previews)} rows. "
            f"Batch {validation.batch.manifest.batch_id} is ready for import."
        )
        if st.button("Import validated batch", type="primary"):
            with st.status(
                "Importing into FalkorDB and verifying graph", expanded=True
            ) as status_box:
                try:
                    result = import_validated_csv(
                        validation,
                        sources,
                        mode=mode,
                        as_of=as_of,
                        graph_name=target,
                        revision=revision,
                        source_timezone=source_timezone,
                    )
                    st.write(f"Result: {result.status}; source counts: {result.counts}")
                    status_box.update(label=f"Import {result.status}", state="complete")
                    _clear_cached_work()
                    if mode == "base":
                        st.session_state.selected_graph = target
                        st.session_state.new_graph = new_graph_name()
                    _upload_steps(3, steps_slot)
                    st.success("Graph verification passed. Navigate to Overview or Investigate.")
                except Exception as error:
                    status_box.update(label="Import did not complete", state="error")
                    st.error(_service_error(error))
                    st.info("Inspect Import History and retry the original batch after repair.")


def _investigate(store, graph, state):
    _page_heading("Investigate", "Follow a refund through real graph connections and evidence.")
    if state["status"] != "READY":
        _badge(state["status"])
        return
    choices = refund_ids(store, as_of=state["as_of"], window_days=30, limit=500)
    st.caption("Chooser shows up to 500 sorted refunds; search by ID for another stored refund.")
    if not choices:
        st.info("No refunds in the default 30-day window. Append refund records to investigate.")
    chosen = st.selectbox("Select a stored refund", ["Type a refund ID", *choices])
    with st.form("assessment_form"):
        refund_id = st.text_input("Refund ID", value="" if chosen == "Type a refund ID" else chosen)
        as_of_text = st.text_input(
            "Assessment time (UTC)", utc(state["as_of"]).replace(" UTC", "Z").replace(" ", "T")
        )
        days = st.number_input("Window days", min_value=1, max_value=3650, value=30)
        assess = st.form_submit_button("Assess refund", type="primary")
    key = (graph, state["revision"], refund_id, as_of_text, int(days), "v1")
    if assess:
        try:
            result = investigate(
                store,
                refund_id,
                as_of=parse_as_of(as_of_text),
                config=InvestigationConfig(window_days=int(days)),
            )
            st.session_state.assessment_key, st.session_state.assessment = key, result
            st.session_state.pop("case_action", None)
        except Exception as error:
            st.error(_service_error(error))
    result = (
        st.session_state.get("assessment")
        if st.session_state.get("assessment_key") == key
        else None
    )
    if result is None:
        st.caption("Assess a refund to see graph paths, rules, timeline, and review context.")
        return
    if result.completeness != "COMPLETE":
        st.error("INCOMPLETE assessment · no final score or priority")
        st.write([error.message for error in result.errors])
        return
    st.markdown(
        _chip(f"{result.priority} review priority", result.priority.lower()),
        unsafe_allow_html=True,
    )
    st.metric("Review priority", f"{result.priority} · {result.total_score}/100")
    st.caption(
        "Demonstration priority, not a fraud probability or proof. "
        f"Window {utc(result.window.start)} through {utc(result.window.end)}, inclusive."
    )
    st.write(result.explanation)
    st.subheader("Rule measurements")
    st.dataframe(
        [
            {
                "rule": signal.rule_id,
                "triggered": signal.triggered,
                "points": signal.contribution,
                "thresholds": signal.thresholds,
                "source_ids": ", ".join(signal.source_ids[:12]),
            }
            for signal in result.analysis.rules
        ],
        hide_index=True,
    )
    st.subheader("Connected accounts")
    st.dataframe(
        [
            {
                "account_id": c.id,
                "depth": c.depth,
                "reached_from": c.reached_from,
                "path": " → ".join(c.seed_path["nodes"]) if c.seed_path else c.id,
            }
            for c in result.candidates
        ],
        hide_index=True,
    )
    st.subheader("Refund timeline")
    burst_refunds = {id_ for burst in result.analysis.bursts for id_ in burst.refund_ids}
    refund_events = sorted(
        (
            record
            for record in result.source_records.refunds
            if result.window.start <= record.requested_at <= result.window.end
        ),
        key=lambda record: (record.requested_at, record.id),
    )
    if refund_events:
        st.dataframe(
            [
                {
                    "requested_at": utc(record.requested_at),
                    "refund_id": record.id,
                    "account_id": record.account_id,
                    "order_id": record.order_id,
                    "amount_inr": record.amount_minor / 100,
                    "qualifying_burst": record.id in burst_refunds,
                }
                for record in refund_events
            ],
            hide_index=True,
        )
    if result.analysis.bursts:
        st.dataframe(
            [
                {
                    "start": utc(b.start),
                    "end": utc(b.end),
                    "accounts": ", ".join(b.account_ids),
                    "refunds": ", ".join(b.refund_ids),
                }
                for b in result.analysis.bursts
            ],
            hide_index=True,
        )
    else:
        st.caption("No qualifying three-account bursts in this window.")
    st.subheader("Graph evidence")
    selected_path = st.selectbox(
        "Highlight an evidence path",
        [None, *[proof.id for proof in result.paths[:200]]],
        format_func=lambda value: value or "All paths",
    )
    projection = graph_projection(result, selected_path_id=selected_path)
    if projection["nodes"]:
        st.echarts_chart(
            echarts_spec(projection),
            height=560,
            alt="Directed graph of actual refund, order, account, device, and address paths",
        )
        st.caption(
            "Drag to pan; scroll to zoom. Arrows show relationship direction. "
            f"Omitted {projection['omitted_nodes']} nodes and "
            f"{projection['omitted_edges']} edges; analysis is unaffected."
        )
        st.caption(
            "Entity legend: teal Account · blue Order · violet Device · "
            "indigo Address · pink Refund"
        )
        node_id = st.selectbox("Node details", sorted(projection["node_details"]))
        st.json(projection["node_details"][node_id])
        st.dataframe(
            [
                {"source": edge["source"], "relationship": edge["name"], "target": edge["target"]}
                for edge in projection["edges"]
            ],
            hide_index=True,
        )
    st.subheader("Evidence references")
    st.dataframe(
        [
            {
                "evidence_id": finding.evidence.id,
                "rule": finding.evidence.rule_id,
                "source_ids": ", ".join(finding.evidence.source_ids),
                "path_ids": ", ".join(finding.path_ids),
            }
            for finding in result.triggered_rules
        ],
        hide_index=True,
    )
    if selected_path:
        proof = next(p for p in result.paths if p.id == selected_path)
        st.json(stable_path(proof.path))
    try:
        context = compare_evidence(store, result)
        st.subheader("Prior reviews and new evidence")
        st.write(
            f"{len(context.reviews)} reviews · {len(context.benign_covered)} covered facts · "
            f"{len(context.material_new)} new facts. Recommendation: {context.recommendation}."
        )
        if context.reviews:
            st.dataframe(
                [
                    {"review_id": r.id, "outcome": r.outcome, "explanation": r.explanation}
                    for r in context.reviews
                ],
                hide_index=True,
            )
        if context.material_new:
            st.dataframe(
                [
                    {
                        "rule": fact.rule_id,
                        "evidence_id": fact.evidence_id,
                        "source_ids": ", ".join(fact.source_ids),
                    }
                    for fact in context.facts
                    if fact.fingerprint in context.material_new
                ],
                hide_index=True,
            )
    except Exception as error:
        st.warning("Review comparison unavailable: " + _service_error(error))
    for limitation in result.limitations:
        st.caption("Limitation: " + limitation)
    if st.button("Investigate and apply case policy", type="primary"):
        try:
            action = cases.apply_case_policy(store, result)
            st.session_state.case_action = action
            st.session_state.pop("scan", None)
        except Exception as error:
            st.error(_service_error(error))
    action = st.session_state.get("case_action")
    if action:
        st.info(f"Case action: {action.action}; case {action.case_id or 'none'}. {action.reason}")
    st.subheader("Agent explanation (optional)")
    settings = AgentSettings()
    configured = bool(
        (
            settings.anthropic_api_key
            if settings.agent_provider == "anthropic"
            else settings.openai_api_key
        )
        and settings.agent_model
    )
    if not configured:
        st.caption("No API key/model configured. Graph investigation and cases remain available.")
    with st.form("agent_form"):
        question = st.text_input(
            "Follow-up question", "Explain the strongest graph connection and prior reviews"
        )
        ask = st.form_submit_button("Ask agent", disabled=not configured)
    if ask:
        try:
            provider = create_provider(settings)
            try:
                response = run_agent(
                    store,
                    f"For refund {refund_id}: {question}",
                    mode="assess",
                    provider=provider,
                    settings=settings,
                )
            finally:
                provider.close()
            st.session_state.agent_result = response
        except Exception as error:
            st.error(_service_error(error))
    response = st.session_state.get("agent_result")
    if response:
        st.write(f"Agent run {response.run_id}: {response.status}")
        st.text(response.response)
        st.dataframe(response.activities, hide_index=True)


def _cases_reviews(store, graph, state):
    _page_heading("Cases & Reviews", "Track case history and preserve each human decision.")
    if state["status"] != "READY":
        _badge(state["status"])
        return
    listing = cases.list_cases(store)
    if listing.cases:
        st.dataframe(
            [
                {
                    "case_id": c.investigation.id,
                    "status": c.investigation.status,
                    "priority": c.investigation.priority,
                    "score": c.investigation.score,
                    "assessments": c.assessment_count,
                }
                for c in listing.cases
            ],
            hide_index=True,
        )
        case_id = st.selectbox("Inspect case", [c.investigation.id for c in listing.cases])
        detail = cases.get_case(store, case_id)
        st.write("Included accounts: " + ", ".join(detail.account_ids))
        st.dataframe(
            [
                {
                    "assessment_id": a.id,
                    "priority": a.priority,
                    "score": a.score,
                    "as_of": utc(a.window_end),
                }
                for a in detail.assessments
            ],
            hide_index=True,
        )
        st.write(f"Case status: {detail.investigation.status}. Reviews stay separate from score.")
        if detail.reviews:
            st.dataframe([r.model_dump(mode="json") for r in detail.reviews], hide_index=True)
    else:
        st.info("No cases yet. LOW findings may still receive a human review without a case.")
        case_id = None
    st.subheader("Record a human review")
    st.caption("Explicit save only. Evidence is recomputed from FalkorDB; the agent cannot review.")
    selected = st.session_state.get("assessment")
    default_refund = (
        selected.seed_refund_id
        if selected and st.session_state.get("assessment_key", (None,))[0] == graph
        else ""
    )
    with st.form("review_form"):
        refund_id = st.text_input("Reviewed refund ID", value=default_refund)
        reviewer_id = st.text_input("Human reviewer identifier")
        outcome = st.selectbox(
            "Outcome", ["BENIGN_EXPLANATION", "NEEDS_MORE_INFORMATION", "CONFIRMED_ABUSE"]
        )
        explanation = st.text_area("Explanation")
        rules = st.multiselect("Evidence rules in scope", ["R1", "R2", "R3", "R4", "R5"])
        associate = st.checkbox("Associate with selected case", disabled=not case_id)
        case_status = st.selectbox(
            "Explicit case status change",
            ["Keep status", "OPEN", "IN_REVIEW", "CLOSED", "REOPENED"],
            disabled=not associate,
        )
        save = st.form_submit_button("Save human review", type="primary")
    if save:
        try:
            request = ReviewRequest(
                refund_id=refund_id,
                reviewer_id=reviewer_id,
                outcome=outcome,
                explanation=explanation,
                rule_ids=rules,
                case_id=case_id if associate else None,
                case_status=case_status if associate and case_status != "Keep status" else None,
            )
            review = record_review(store, request)
            st.success(f"Review {review.id} saved. Prior history is preserved.")
            st.session_state.pop("scan", None)
        except Exception as error:
            st.error(_service_error(error))
    st.subheader("Scoped review history")
    history = review_history(store, limit=1000)
    if history:
        st.dataframe(
            [
                {
                    "review_id": r.id,
                    "reviewer": r.reviewer_id,
                    "outcome": r.outcome,
                    "assessment_id": r.assessment_id,
                    "case_id": r.case_id,
                    "reviewed_at": utc(r.reviewed_at),
                    "supersedes": r.supersedes,
                }
                for r in history
            ],
            hide_index=True,
        )
    else:
        st.caption("No recorded human reviews.")


def _history(store, state):
    _page_heading("Import History", "See every batch, its outcome, and the available data horizon.")
    _badge(state["status"])
    if not state["batches"]:
        st.info("No import batches yet.")
        return
    st.caption(f"Through {utc(state['as_of'])}; revision {state['revision'] or 'unavailable'}")
    st.dataframe(
        [
            {
                **batch,
                "started_at": utc(batch["started_at"]),
                "completed_at": utc(batch["completed_at"]),
            }
            for batch in state["batches"]
        ],
        hide_index=True,
    )
    if state["status"] != "READY":
        st.warning("Partial writes may exist. Retry the original batch before assessment.")


def main():
    st.set_page_config(page_title="RingLens · Investigation desk", page_icon="🔎", layout="wide")
    st.markdown(
        f"<style>{STYLE_PATH.read_text(encoding='utf-8')}</style>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        '<div class="rl-brand"><div class="rl-brand-mark" aria-hidden="true">'
        '<svg width="28" height="28" viewBox="0 0 28 28" fill="none" '
        'xmlns="http://www.w3.org/2000/svg"><path d="M7 9L20 7M8 11L13 21M19 10L15 20" '
        'stroke="currentColor" stroke-width="2"/><circle cx="6" cy="9" r="3" fill="currentColor"/>'
        '<circle cx="21" cy="7" r="3" fill="currentColor"/><circle cx="14" cy="22" r="3" '
        'fill="currentColor"/></svg></div><div><div class="rl-brand-name">RingLens</div>'
        '<div class="rl-brand-subtitle">Investigation workspace</div></div></div>',
        unsafe_allow_html=True,
    )
    try:
        names = available_graphs()
        initial = st.session_state.get("selected_graph")
        graph = st.sidebar.selectbox(
            "Dataset graph", names, index=names.index(initial) if initial in names else 0
        )
        st.session_state.selected_graph = graph
        section = st.sidebar.radio(
            "Workspace",
            ["Overview", "Upload Data", "Investigate", "Cases & Reviews", "Import History"],
            format_func=lambda name: f"{NAV_ICONS[name]}  {name}",
        )
        with selected_store(graph) as store:
            state = dataset_status(store)
            status = state["status"]
            tone = "ready" if status == "READY" else "pending" if status == "EMPTY" else "warning"
            st.sidebar.markdown(
                '<div class="rl-sidebar-context"><small>SELECTED DATASET</small>'
                f"<strong>{escape(state['dataset_id'] or 'No dataset yet')}</strong>"
                f"{_chip(status, tone)}"
                f'<div class="rl-through">Data through · {escape(utc(state["as_of"]))}</div>'
                "</div>",
                unsafe_allow_html=True,
            )
            st.sidebar.caption("Synthetic demo data · private local workspace")
            if section == "Overview":
                _overview(store, state)
            elif section == "Upload Data":
                _upload(store, graph, state)
            elif section == "Investigate":
                _investigate(store, graph, state)
            elif section == "Cases & Reviews":
                _cases_reviews(store, graph, state)
            else:
                _history(store, state)
    except Exception as error:
        st.error(_service_error(error))
        st.info("Start FalkorDB with `docker compose up -d --wait`, then refresh.")


if __name__ == "__main__":
    main()
