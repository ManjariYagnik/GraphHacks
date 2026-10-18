"""CSV-to-insights workflow on disposable FalkorDB graphs; no LLM calls."""

import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from ringlens import streamlit_app
from ringlens.agent_provider import AgentSettings, ProviderTurn
from ringlens.cases import apply_case_policy, get_case
from ringlens.config import Settings
from ringlens.csv_adapter import CSVSource, csv_bundle, required_columns, validate_csv_sources
from ringlens.dashboard_data import (
    available_graphs,
    dataset_status,
    import_validated_csv,
    new_graph_name,
    overview,
    scan_refunds,
    selected_store,
)
from ringlens.dataset_io import read_batch
from ringlens.graph_store import GraphStore
from ringlens.graph_view import graph_projection
from ringlens.investigator import investigate
from ringlens.models import FILE_KINDS
from ringlens.review_models import ReviewRequest
from ringlens.reviews import compare_evidence, record_review

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def base_csv_sources():
    with zipfile.ZipFile(io.BytesIO(csv_bundle(sample=True))) as archive:
        return [CSVSource(f"{kind}.csv", kind, archive.read(f"{kind}.csv")) for kind in FILE_KINDS]


def append_csv_source():
    batch = read_batch(ROOT / "data/demo/new_evidence")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    columns = required_columns("refunds")
    writer.writerow(columns)
    for record in batch.records.refunds:
        values = record.model_dump()
        writer.writerow([values[column] for column in columns])
    return CSVSource("refunds.csv", "refunds", output.getvalue().encode())


@pytest.fixture
def graph(tmp_path):
    name = new_graph_name()
    registry = tmp_path / "registry.json"
    with GraphStore(Settings(graph_name=name)) as store:
        assert not store.graph_exists()
        try:
            yield name, registry, store
        finally:
            if name in store._db.list_graphs():
                store._db.select_graph(name).delete()


def test_csv_base_append_metrics_paths_and_idempotency(graph):
    name, registry, store = graph
    base = base_csv_sources()
    as_of = read_batch(ROOT / "data/demo/base").manifest.as_of
    validated = validate_csv_sources(base, mode="base", as_of=as_of, store=store, graph=name)
    assert validated.valid and not store.graph_exists()
    first = import_validated_csv(
        validated, base, mode="base", as_of=as_of, graph_name=name, registry_path=registry
    )
    assert first.status == "READY" and first.counts["refunds"] == 42
    assert name in available_graphs(registry)
    replay = import_validated_csv(
        validated, base, mode="base", as_of=as_of, graph_name=name, registry_path=registry
    )
    assert replay.status == "NO_OP"
    with selected_store(name, path=registry) as selected:
        before = overview(selected)
        assert before["ready"] and before["counts"]["accounts"] == 50
        assert before["counts"]["orders"] == 250
        assert before["refunded_orders"] == 42
        assert before["refunded_order_percent"] == 16.8
        assert dataset_status(selected)["status"] == "READY"
        scan = scan_refunds(selected, as_of=as_of, max_seeds=5)
        assert scan["assessed"] == 5 and scan["skipped"] == 37
        result = investigate(selected, "ref_9082163771ef0a5e5b26")
        assert result.completeness == "COMPLETE" and any(c.depth == 2 for c in result.candidates)
        assert dataset_status(selected)["revision"] == result.input_revision
        projection = graph_projection(result, max_nodes=100, max_edges=150)
        assert projection["edges"] and projection["omitted_edges"] >= 0
        assert all(
            node["name"] == projection["short_ids"][node["id"]] for node in projection["nodes"]
        )
        assert len({node["name"] for node in projection["nodes"]}) == len(projection["nodes"])
        assert (
            projection["short_ids"]
            == graph_projection(result, selected_path_id=result.paths[-1].id, max_nodes=3)[
                "short_ids"
            ]
        )
        actual = {(edge["source"], edge["name"], edge["target"]) for edge in projection["edges"]}
        assert actual <= {
            (edge[0], edge[1], edge[2])
            for path in result.paths
            for edge in [(e["source_id"], e["type"], e["target_id"]) for e in path.path["edges"]]
        }
        household = investigate(selected, "ref_4d44e5cc3e0a8dae77fd")
        assert household.priority == "LOW"
        review = record_review(
            selected,
            ReviewRequest(
                refund_id=household.seed_refund_id,
                reviewer_id="dashboard-test-reviewer",
                outcome="BENIGN_EXPLANATION",
                explanation="Shared resources reviewed; later refunds remain in scope.",
                rule_ids=["R1", "R5"],
            ),
        )
        assert selected.read("MATCH (i:Investigation) RETURN count(i)").result_set == [[0]]
    append = [append_csv_source()]
    later = read_batch(ROOT / "data/demo/new_evidence").manifest.as_of
    revision = dataset_status(store)["revision"]
    prepared = validate_csv_sources(
        append, mode="append", as_of=later, store=store, graph=name, revision=revision
    )
    assert prepared.valid
    imported = import_validated_csv(
        prepared,
        append,
        mode="append",
        as_of=later,
        graph_name=name,
        revision=revision,
        registry_path=registry,
    )
    assert imported.status == "READY" and imported.counts["refunds"] == 48
    assert dataset_status(store)["revision"] != revision
    reassessed = investigate(store, household.seed_refund_id)
    context = compare_evidence(store, reassessed)
    assert reassessed.priority == "HIGH" and any(r.id == review.id for r in context.reviews)
    assert {fact.rule_id for fact in context.facts if fact.fingerprint in context.material_new} >= {
        "R2",
        "R3",
    }
    action = apply_case_policy(store, reassessed)
    assert action.action == "CREATED"
    assert get_case(store, action.case_id).investigation.status == "OPEN"
    current_revision = dataset_status(store)["revision"]
    replayed_validation = validate_csv_sources(
        append,
        mode="append",
        as_of=later,
        store=store,
        graph=name,
        revision=current_revision,
    )
    assert replayed_validation.valid
    replayed_append = import_validated_csv(
        replayed_validation,
        append,
        mode="append",
        as_of=later,
        graph_name=name,
        revision=current_revision,
        registry_path=registry,
    )
    assert replayed_append.status == "NO_OP" and replayed_append.counts["refunds"] == 48
    with pytest.raises(ValueError, match="revision changed"):
        import_validated_csv(
            prepared,
            append,
            mode="append",
            as_of=later,
            graph_name=name,
            revision=revision,
            registry_path=registry,
        )


def test_invalid_csv_has_no_graph_writes(graph):
    name, _, store = graph
    files = base_csv_sources()
    bad = files[-2]
    files[-2] = CSVSource(
        bad.filename, bad.kind, bad.content.replace(b"acct_", b"acct_missing_", 1)
    )
    as_of = read_batch(ROOT / "data/demo/base").manifest.as_of
    result = validate_csv_sources(files, mode="base", as_of=as_of, store=store, graph=name)
    assert not result.valid and result.batch is None and not store.graph_exists()


def test_append_rejects_orphans_conflicts_currency_and_chronology_before_writes(graph):
    name, registry, store = graph
    base = base_csv_sources()
    as_of = read_batch(ROOT / "data/demo/base").manifest.as_of
    validated = validate_csv_sources(base, mode="base", as_of=as_of, store=store, graph=name)
    import_validated_csv(
        validated, base, mode="base", as_of=as_of, graph_name=name, registry_path=registry
    )
    append_batch = read_batch(ROOT / "data/demo/new_evidence")
    refund = append_batch.records.refunds[0]
    later = append_batch.manifest.as_of
    revision = dataset_status(store)["revision"]
    before = store.read("MATCH (r:Refund) RETURN count(r)").result_set[0][0]
    changes = [
        {"account_id": "acct_missing"},
        {"order_id": "ord_missing"},
        {"currency": "USD"},
        {"requested_at": 1},
        {"amount_minor": 2**63 - 1},
    ]
    for changed in changes:
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        columns = required_columns("refunds")
        writer.writerow(columns)
        values = {**refund.model_dump(), **changed}
        writer.writerow([values[column] for column in columns])
        source = CSVSource("refunds.csv", "refunds", output.getvalue().encode())
        checked = validate_csv_sources(
            [source], mode="append", as_of=later, store=store, graph=name, revision=revision
        )
        assert not checked.valid
        assert checked.batch is None
        assert store.read("MATCH (r:Refund) RETURN count(r)").result_set[0][0] == before
    original = read_batch(ROOT / "data/demo/base").records.refunds[0]
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    columns = required_columns("refunds")
    writer.writerow(columns)
    values = {**original.model_dump(), "amount_minor": 1}
    writer.writerow([values[column] for column in columns])
    conflict = validate_csv_sources(
        [CSVSource("refunds.csv", "refunds", output.getvalue().encode())],
        mode="append",
        as_of=later,
        store=store,
        graph=name,
        revision=revision,
    )
    assert not conflict.valid
    assert dataset_status(store)["status"] == "READY"


def test_ui_rerenders_do_not_repeat_writes(graph, monkeypatch):
    name, registry, store = graph
    files = base_csv_sources()
    as_of = read_batch(ROOT / "data/demo/base").manifest.as_of
    validated = validate_csv_sources(files, mode="base", as_of=as_of, store=store, graph=name)
    import_validated_csv(
        validated, files, mode="base", as_of=as_of, graph_name=name, registry_path=registry
    )
    monkeypatch.setattr(streamlit_app, "available_graphs", lambda: [name])
    monkeypatch.setattr(
        streamlit_app, "selected_store", lambda selected: GraphStore(Settings(graph_name=selected))
    )

    def counts():
        return store.read(
            "MATCH (n) WHERE n:ImportBatch OR n:Investigation OR n:Review OR n:AgentRun "
            "RETURN count(n)"
        ).result_set[0][0]

    before = counts()

    def app_entry():
        from ringlens import streamlit_app as dashboard

        dashboard.main()

    app = AppTest.from_function(app_entry, default_timeout=15).run()
    assert not app.exception
    app.run()
    assert not app.exception
    app.sidebar.radio[0].set_value("Upload Data").run()
    assert not app.exception
    app.sidebar.radio[0].set_value("Cases & Reviews").run()
    assert not app.exception
    app.sidebar.radio[0].set_value("Import History").run()
    assert not app.exception
    app.sidebar.radio[0].set_value("Investigate").run()
    assert not app.exception
    next(widget for widget in app.selectbox if widget.label == "Select a stored refund").set_value(
        "ref_9082163771ef0a5e5b26"
    ).run()
    next(button for button in app.button if button.label == "Assess refund").click().run()
    assert not app.exception
    assert any("HIGH" in metric.value for metric in app.metric)
    assert counts() == before


def test_upload_validation_survives_clock_change_and_rerun(graph, monkeypatch):
    name, _, _ = graph
    files = base_csv_sources()
    uploads = [
        SimpleNamespace(name=source.filename, getvalue=lambda data=source.content: data)
        for source in files
    ]
    monkeypatch.setattr(streamlit_app, "available_graphs", lambda: [name])
    monkeypatch.setattr(
        streamlit_app,
        "selected_store",
        lambda selected: GraphStore(Settings(graph_name=selected)),
    )
    monkeypatch.setattr(streamlit_app.st, "file_uploader", lambda *args, **kwargs: uploads)

    class Clock(datetime):
        now_seconds = int(datetime(2026, 10, 18, tzinfo=UTC).timestamp())

        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(cls.now_seconds, tz=tz)

    monkeypatch.setattr(streamlit_app, "datetime", Clock)

    def app_entry():
        from ringlens import streamlit_app as dashboard

        dashboard.main()

    app = AppTest.from_function(app_entry, default_timeout=15).run()
    app.sidebar.radio[0].set_value("Upload Data").run()
    assert not app.exception
    next(widget for widget in app.text_input if widget.label == "Data through (UTC)").set_value(
        "2026-10-15T10:00:00Z"
    ).run()
    next(button for button in app.button if button.label == "Preview and validate").click().run()
    assert not app.exception
    assert any(button.label == "Import validated batch" for button in app.button)

    Clock.now_seconds += 5
    app.run()
    assert not app.exception
    as_of_widget = next(widget for widget in app.text_input if widget.label == "Data through (UTC)")
    assert as_of_widget.value == "2026-10-15T10:00:00Z"
    assert any(button.label == "Import validated batch" for button in app.button)
    assert not any("Inputs or settings changed" in warning.value for warning in app.warning)

    as_of_widget.set_value("2026-10-15T11:00:00Z").run()
    assert any("Inputs or settings changed" in warning.value for warning in app.warning)
    assert not any(button.label == "Import validated batch" for button in app.button)
    next(button for button in app.button if button.label == "Preview and validate").click().run()
    assert not app.exception
    assert any(button.label == "Import validated batch" for button in app.button)


def test_frontend_agent_investigates_and_confirms_case_action(graph, monkeypatch):
    name, registry, store = graph
    files = base_csv_sources()
    as_of = read_batch(ROOT / "data/demo/base").manifest.as_of
    validated = validate_csv_sources(files, mode="base", as_of=as_of, store=store, graph=name)
    import_validated_csv(
        validated, files, mode="base", as_of=as_of, graph_name=name, registry_path=registry
    )
    monkeypatch.setattr(streamlit_app, "available_graphs", lambda: [name])
    monkeypatch.setattr(
        streamlit_app,
        "selected_store",
        lambda selected: GraphStore(Settings(graph_name=selected)),
    )
    fake_settings = AgentSettings(
        _env_file=None,
        agent_provider="anthropic",
        RINGLENS_ANTHROPIC_API_KEY="test-only",
        agent_model="mock-model",
    )
    monkeypatch.setattr(streamlit_app, "AgentSettings", lambda: fake_settings)
    seed = "ref_9082163771ef0a5e5b26"

    class DemoProvider:
        def next_turn(self, history, schemas, mode):
            outputs = [
                json.loads(item["output"])
                for item in history
                if item.get("type") == "function_call_output"
            ]
            if not outputs:
                name, args = "analyze_refund", {"refund_id": seed}
            elif len(outputs) == 1:
                name, args = (
                    "get_review_context",
                    {"assessment_reference": outputs[0]["data"]["assessment_reference"]},
                )
            elif len(outputs) == 2 and mode == "investigate":
                name, args = (
                    "apply_case_policy",
                    {"assessment_reference": outputs[0]["data"]["assessment_reference"]},
                )
            else:
                return ProviderTurn(finished=True)
            call = {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps(args),
                "call_id": f"call_{len(outputs)}",
            }
            return ProviderTurn(items=[call], calls=[call])

        def close(self):
            pass

    monkeypatch.setattr(streamlit_app, "create_provider", lambda settings: DemoProvider())

    def app_entry():
        from ringlens import streamlit_app as dashboard

        dashboard.main()

    app = AppTest.from_function(app_entry, default_timeout=15).run()
    app.sidebar.radio[0].set_value("Investigate").run()
    next(widget for widget in app.selectbox if widget.label == "Select a stored refund").set_value(
        seed
    ).run()
    next(button for button in app.button if button.label == "Ask agent · read-only").click().run()
    assert not app.exception
    assert app.session_state["agent_result"].status == "COMPLETE"
    assert app.session_state["agent_result"].mode == "assess"
    assert store.read("MATCH (i:Investigation) RETURN count(i)").result_set == [[0]]
    assert store.read("MATCH (r:AgentRun) RETURN count(r)").result_set == [[1]]
    write_button = next(
        button for button in app.button if button.label == "Run agent and apply case policy"
    )
    write_button.click().run()
    assert not app.exception
    result = app.session_state["agent_result"]
    assert result.status == "COMPLETE" and result.mode == "investigate"
    assert [a["action"] for a in result.activities if a["tool"] == "apply_case_policy"] == [
        "CREATED"
    ]
    assert store.read("MATCH (i:Investigation) RETURN count(i)").result_set == [[1]]
    assert store.read("MATCH (r:AgentRun) RETURN count(r)").result_set == [[2]]
    app.run()
    assert not app.exception
    assert store.read("MATCH (r:AgentRun) RETURN count(r)").result_set == [[2]]
    write_button = next(
        button for button in app.button if button.label == "Run agent and apply case policy"
    )
    write_button.click().run()
    assert app.session_state["agent_result"].status == "COMPLETE"
    assert "EXISTING_UNCHANGED" in app.session_state["agent_result"].response
    assert store.read("MATCH (i:Investigation) RETURN count(i)").result_set == [[1]]
