"""Disposable real-graph case workflow and evaluation; no production label access."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from ringlens import case_queries as q
from ringlens.case_models import CaseRecord
from ringlens.cases import apply_case_policy, get_case, list_cases
from ringlens.cli import app
from ringlens.config import Settings
from ringlens.dataset_io import make_batch, read_batch
from ringlens.graph_store import GraphConnectionError, GraphStore
from ringlens.ingest import ingest_batch, ingest_directory
from ringlens.investigation_models import InvestigationConfig, ScoringConfig
from ringlens.investigator import investigate
from ringlens.models import Account, Order, Review
from ringlens.schema import INDEXES, initialize_schema
from ringlens.validation import SourceRecords
from ringlens.verify import verify_graph

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
BASE, APPEND = ROOT / "data/demo/base", ROOT / "data/demo/new_evidence"


@pytest.fixture
def graph():
    name = f"ringlens_test_cases_{uuid4().hex}"
    settings = Settings(graph_name=name)
    with GraphStore(settings) as store:
        assert not store.graph_exists(), "Refusing an existing graph"
        try:
            yield store, settings
        finally:
            if name in store._db.list_graphs():
                store._db.select_graph(name).delete()
            assert name not in store._db.list_graphs()


@pytest.fixture
def truth():
    return json.loads((ROOT / "evaluation/demo_ground_truth.json").read_text())


def image(store):
    return (
        store.read("MATCH (n) RETURN id(n), labels(n), properties(n) ORDER BY id(n)").result_set,
        store.read(
            "MATCH (a)-[r]->(b) RETURN id(r), id(a), type(r), id(b) ORDER BY id(r)"
        ).result_set,
    )


@pytest.mark.parametrize("scenario", ["pattern_a", "pattern_b"])
def test_create_duplicate_other_seed_readers_and_application_restart(
    graph, truth, scenario, monkeypatch
):
    store, settings = graph
    ingest_directory(store, BASE)
    expected = truth["scenarios"][scenario]
    result = investigate(store, expected["seed_refund_id"])
    before = image(store)
    assert result.total_score == 90 and result.priority == "HIGH"
    assert image(store) == before
    with monkeypatch.context() as patch:

        def no_files(*args, **kwargs):
            raise AssertionError("Case persistence must never read labels/source files")

        patch.setattr(Path, "read_text", no_files)
        patch.setattr(Path, "read_bytes", no_files)
        created = apply_case_policy(store, result)
    assert created.action == "CREATED", created.reason
    detail = get_case(store, created.case_id)
    assert detail.investigation.status == "OPEN" and detail.investigation.score == 90
    assert detail.investigation.latest_assessment_id == created.assessment_id
    assert detail.account_ids == sorted(expected["account_ids"])
    assert (
        len(detail.assessments) == 1
        and len(detail.evidence_by_assessment[created.assessment_id]) == 4
    )
    snapshot = json.loads(detail.assessments[0].snapshot_json)
    assert snapshot["seed_refund_id"] == expected["seed_refund_id"]
    assert all(
        "graph_id" not in node for path in snapshot["paths"] for node in path["path"]["nodes"]
    )
    before = image(store)
    assert apply_case_policy(store, result).action == "EXISTING_UNCHANGED"
    alternative_id = next(
        id_ for id_ in expected["base_refund_ids"] if id_ != result.seed_refund_id
    )
    alternative = investigate(store, alternative_id)
    assert alternative.triggered_rules == result.triggered_rules
    reused = apply_case_policy(store, alternative)
    assert reused.action == "EXISTING_UNCHANGED", reused.reason
    assert reused.case_id == created.case_id and reused.assessment_id == created.assessment_id
    assert image(store) == before
    assert list_cases(store).cases[0].assessment_count == 1
    with GraphStore(settings) as restarted_application:
        assert get_case(restarted_application, created.case_id) == detail
    process = subprocess.run(
        [
            str(ROOT / ".venv/bin/ringlens"),
            "cases",
            "show",
            "--case-id",
            created.case_id,
            "--graph",
            settings.graph_name,
            "--format",
            "json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["investigation"]["id"] == created.case_id
    assert verify_graph(store).ok
    # All discovery explanations start at the seed and include bridge segments.
    for candidate in result.candidates:
        assert candidate.seed_path["nodes"][0] == result.seed_account_id
        assert candidate.seed_path["nodes"][-1] == candidate.id
        assert len(candidate.seed_path["edges"]) == candidate.depth * 4
    ingest_directory(store, APPEND)
    appended = investigate(store, expected["seed_refund_id"])
    updated = apply_case_policy(store, appended)
    assert updated.action == "UPDATED" and updated.case_id == created.case_id, updated.reason
    saved = get_case(store, created.case_id)
    assert len(saved.assessments) == 2 and saved.investigation.score == 90
    assert (
        next(a for a in saved.assessments if a.id == created.assessment_id) == detail.assessments[0]
    )
    assert (
        apply_case_policy(store, investigate(store, alternative_id)).action == "EXISTING_UNCHANGED"
    )
    assert verify_graph(store).ok


def test_household_low_no_case_then_append_enables_creation(graph, truth):
    store, _ = graph
    ingest_directory(store, BASE)
    seed = truth["scenarios"]["household"]["seed_refund_id"]
    before = image(store)
    low = investigate(store, seed)
    assert low.total_score == 35
    assert apply_case_policy(store, low).action == "NO_CASE_REQUIRED"
    assert image(store) == before and not list_cases(store).cases
    ingest_directory(store, APPEND)
    raised = investigate(store, seed)
    assert raised.total_score == 100
    created = apply_case_policy(store, raised)
    assert created.action == "CREATED", created.reason
    assert get_case(store, created.case_id).investigation.priority == "HIGH"


def add_review(store, case_id, evidence_id, at):
    review = Review(
        id="rev_preserved",
        outcome="BENIGN_EXPLANATION",
        explanation="Synthetic reviewer explanation retained across reassessment.",
        reviewed_at=at,
    )
    store.write(
        "MATCH (i:Investigation {id: $case_id}), (e:Evidence {id: $evidence_id}) "
        "CREATE (r:Review) SET r = $review "
        "CREATE (i)-[:HAS_REVIEW]->(r), (r)-[:ABOUT]->(e)",
        {"case_id": case_id, "evidence_id": evidence_id, "review": review.model_dump()},
    )
    return review


def test_append_history_reviews_closed_status_and_lower_priority_updates(graph, truth):
    store, _ = graph
    ingest_directory(store, BASE)
    seed = truth["scenarios"]["pattern_a"]["seed_refund_id"]
    base_result = investigate(store, seed)
    original = apply_case_policy(store, base_result)
    assert original.action == "CREATED", original.reason
    before = get_case(store, original.case_id)
    review = add_review(
        store,
        original.case_id,
        before.evidence_by_assessment[original.assessment_id][0].id,
        truth["base_as_of"],
    )
    store.write(
        "MATCH (i:Investigation {id: $id}) SET i.status = 'CLOSED'", {"id": original.case_id}
    )
    ingest_directory(store, APPEND)
    assert verify_graph(store).ok
    before_stale = image(store)
    assert apply_case_policy(store, base_result).action == "NOT_CREATED_INCOMPLETE"
    assert image(store) == before_stale
    updated = apply_case_policy(store, investigate(store, seed))
    assert updated.action == "UPDATED" and updated.assessment_id != original.assessment_id, (
        updated.reason
    )
    after = get_case(store, original.case_id)
    assert after.investigation.status == "CLOSED" and after.investigation.requires_review
    assert after.reviews == [review] and len(after.assessments) == 2
    assert (
        next(a for a in after.assessments if a.id == original.assessment_id)
        == before.assessments[0]
    )
    # Configuration changes belong to a different immutable assessment, same group.
    low = investigate(
        store, seed, config=InvestigationConfig(scoring=ScoringConfig(min_accounts=100))
    )
    assert low.priority == "LOW" and low.total_score == 0
    lowered = apply_case_policy(store, low)
    assert lowered.action == "UPDATED", lowered.reason
    saved = get_case(store, original.case_id)
    assert len(saved.assessments) == 3 and saved.investigation.score == 0
    assert saved.investigation.status == "CLOSED" and saved.reviews == [review]
    assert saved.investigation.latest_assessment_id == lowered.assessment_id
    assert apply_case_policy(store, low).action == "EXISTING_UNCHANGED"


def test_stale_revision_and_notready_prevent_any_case_writes(graph, truth):
    store, _ = graph
    ingest_directory(store, BASE)
    seed = truth["scenarios"]["pattern_a"]["seed_refund_id"]
    result = investigate(store, seed)
    ingest_directory(store, APPEND)
    before = image(store)
    action = apply_case_policy(store, result)
    assert action.action == "NOT_CREATED_INCOMPLETE" and "fresh" in action.reason
    assert image(store) == before
    current = investigate(store, seed)
    store.write("MATCH (d:Dataset) SET d.status = 'INGESTING'")
    before = image(store)
    assert apply_case_policy(store, current).action == "NOT_CREATED_INCOMPLETE"
    assert image(store) == before


@pytest.mark.parametrize("cap", ["accounts", "evidence"])
def test_incomplete_never_persists_cases(graph, truth, cap):
    store, _ = graph
    ingest_directory(store, BASE)
    config = (
        InvestigationConfig(max_accounts=2)
        if cap == "accounts"
        else InvestigationConfig(max_evidence=2)
    )
    result = investigate(store, truth["scenarios"]["pattern_b"]["seed_refund_id"], config=config)
    before = image(store)
    assert result.completeness == "INCOMPLETE"
    assert apply_case_policy(store, result).action == "NOT_CREATED_INCOMPLETE"
    assert image(store) == before


@pytest.mark.parametrize("fail_query", [q.START, q.EVIDENCE, q.PUBLISH_WITH_EVIDENCE])
def test_interrupted_persistence_hidden_until_retry_and_no_duplicates(
    graph, truth, monkeypatch, fail_query
):
    store, _ = graph
    ingest_directory(store, BASE)
    result = investigate(store, truth["scenarios"]["pattern_a"]["seed_refund_id"])
    real_write = store.write

    def fail(query, params=None, **kwargs):
        if query == fail_query:
            raise GraphConnectionError("Controlled case-write failure")
        return real_write(query, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", fail)
        action = apply_case_policy(store, result)
    assert action.action == "NOT_CREATED_INCOMPLETE", action.reason
    assert not list_cases(store).cases
    assert store.read("MATCH (a:Assessment {status: 'READY'}) RETURN count(a)").result_set == [[0]]
    retried = apply_case_policy(store, result)
    assert retried.action == "CREATED", retried.reason
    assert len(get_case(store, retried.case_id).assessments) == 1
    assert apply_case_policy(store, result).action == "EXISTING_UNCHANGED"


def test_false_success_missing_evidence_endpoint_does_not_publish(graph, truth, monkeypatch):
    store, _ = graph
    ingest_directory(store, BASE)
    result = investigate(store, truth["scenarios"]["pattern_a"]["seed_refund_id"])
    real_write = store.write

    def skip(query, params=None, **kwargs):
        if query == q.EVIDENCE:
            return SimpleNamespace(result_set=[[len(params["rows"])]])
        return real_write(query, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", skip)
        action = apply_case_policy(store, result)
    assert action.action == "NOT_CREATED_INCOMPLETE"
    assert "evidence" in action.reason and not list_cases(store).cases
    assert apply_case_policy(store, result).action == "CREATED"


def test_lost_final_ack_reconciles_marker_and_retry_is_unchanged(graph, truth, monkeypatch):
    store, _ = graph
    ingest_directory(store, BASE)
    result = investigate(store, truth["scenarios"]["pattern_a"]["seed_refund_id"])
    real_write = store.write

    def lost_ack(query, params=None, **kwargs):
        value = real_write(query, params, **kwargs)
        if query == q.PUBLISH_WITH_EVIDENCE:
            raise GraphConnectionError("Controlled final acknowledgement loss")
        return value

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", lost_ack)
        first = apply_case_policy(store, result)
    assert first.action == "CREATED", first.reason
    assert "confirmed" in first.reason
    assert apply_case_policy(store, result).action == "EXISTING_UNCHANGED"
    assert len(get_case(store, first.case_id).assessments) == 1


def test_changed_group_overlap_flags_both_cases_without_membership_merge(graph, truth):
    store, _ = graph
    original = read_batch(BASE)
    expected = truth["scenarios"]["pattern_b"]
    ingest_batch(store, original)
    full = apply_case_policy(store, investigate(store, expected["seed_refund_id"]))
    assert full.action == "CREATED", full.reason
    # Add a valid linked account/order in this test only; original membership is immutable.
    order = next(o for o in original.records.orders if o.account_id == expected["account_ids"][0])
    extra = SourceRecords(
        accounts=[Account(id="acct_extra", created_at=original.manifest.as_of - 10)],
        orders=[
            Order(
                id="ord_extra",
                account_id="acct_extra",
                device_id=order.device_id,
                address_id=order.address_id,
                placed_at=original.manifest.as_of - 1,
                amount_minor=1,
                currency="INR",
            )
        ],
    )
    ingest_batch(store, make_batch(extra, seed=1, as_of=original.manifest.as_of + 1, mode="append"))
    found = investigate(store, expected["seed_refund_id"])
    assert set(c.id for c in found.candidates) != set(expected["account_ids"])
    low_group = investigate(
        store,
        expected["seed_refund_id"],
        config=InvestigationConfig(scoring=ScoringConfig(min_accounts=100)),
    )
    low_action = apply_case_policy(store, low_group)
    assert low_action.action == "NO_CASE_REQUIRED" and low_action.requires_review
    assert low_action.overlapping_case_ids == [full.case_id]
    assert get_case(store, full.case_id).investigation.requires_review
    assert len(list_cases(store).cases) == 1
    changed = apply_case_policy(store, found)
    assert changed.action == "CREATED" and changed.case_id != full.case_id, changed.reason
    first, second = get_case(store, full.case_id), get_case(store, changed.case_id)
    assert first.account_ids == sorted(expected["account_ids"])
    assert second.account_ids == sorted(c.id for c in found.candidates)
    assert first.overlapping_case_ids == [changed.case_id] and second.overlapping_case_ids == [
        full.case_id
    ]
    assert first.investigation.requires_review and second.investigation.requires_review


def test_additive_schema_twice_and_cli_round_trip(graph, truth):
    store, settings = graph
    assert initialize_schema(store).created_indexes == len(INDEXES) == 30
    assert initialize_schema(store).created_indexes == 0
    ingest_directory(store, BASE)
    runner = CliRunner()
    seed = truth["scenarios"]["pattern_a"]["seed_refund_id"]
    arguments = [
        "investigate",
        "--refund-id",
        seed,
        "--graph",
        settings.graph_name,
        "--format",
        "json",
    ]
    before = image(store)
    read_only = runner.invoke(app, arguments)
    assert read_only.exit_code == 0 and image(store) == before
    created = runner.invoke(app, [*arguments, "--create-case"])
    assert created.exit_code == 0, created.output
    action = json.loads(created.output)["case_action"]
    assert action["action"] == "CREATED"
    again = runner.invoke(app, [*arguments, "--create-case"])
    assert json.loads(again.output)["case_action"]["action"] == "EXISTING_UNCHANGED"
    listed = runner.invoke(
        app, ["cases", "list", "--graph", settings.graph_name, "--format", "json"]
    )
    assert listed.exit_code == 0 and len(json.loads(listed.output)["cases"]) == 1
    shown = runner.invoke(
        app,
        [
            "cases",
            "show",
            "--case-id",
            action["case_id"],
            "--graph",
            settings.graph_name,
            "--format",
            "json",
        ],
    )
    assert shown.exit_code == 0, shown.output
    assert (
        CaseRecord.model_validate(json.loads(shown.output)["investigation"]).id == action["case_id"]
    )


def test_crash_and_alternative_seed_resume_the_original_snapshot(graph, truth, monkeypatch):
    store, _ = graph
    ingest_directory(store, BASE)
    expected = truth["scenarios"]["pattern_a"]
    result = investigate(store, expected["seed_refund_id"])
    real_write = store.write

    class Crash(BaseException):
        pass

    def crash(query, params=None, **kwargs):
        if query == q.EVIDENCE:
            raise Crash
        return real_write(query, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", crash)
        with pytest.raises(Crash):
            apply_case_policy(store, result)
    assert not list_cases(store).cases and list_cases(store).pending_case_count == 1
    other = next(id_ for id_ in expected["base_refund_ids"] if id_ != result.seed_refund_id)
    retried = apply_case_policy(store, investigate(store, other))
    assert retried.action == "CREATED", retried.reason
    detail = get_case(store, retried.case_id)
    assert len(detail.assessments) == 1 and not detail.pending_assessment_ids
    assert (
        json.loads(detail.assessments[0].snapshot_json)["seed_refund_id"] == result.seed_refund_id
    )


def test_update_failure_keeps_published_assessment_visible_and_final_ready_guard(
    graph, truth, monkeypatch
):
    store, _ = graph
    ingest_directory(store, BASE)
    seed = truth["scenarios"]["pattern_a"]["seed_refund_id"]
    first = apply_case_policy(store, investigate(store, seed))
    original = get_case(store, first.case_id)
    ingest_directory(store, APPEND)
    fresh = investigate(store, seed)
    real_write = store.write

    def changed_before_publish(query, params=None, **kwargs):
        if query == q.PUBLISH_WITH_EVIDENCE:
            real_write("MATCH (d:Dataset) SET d.status = 'INGESTING'")
        return real_write(query, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", changed_before_publish)
        action = apply_case_policy(store, fresh)
    assert action.action == "NOT_CREATED_INCOMPLETE"
    saved = get_case(store, first.case_id)
    assert saved.assessments == original.assessments and len(saved.pending_assessment_ids) == 1
    assert saved.investigation == original.investigation
    store.write("MATCH (d:Dataset) SET d.status = 'READY'")
    retry = apply_case_policy(store, investigate(store, seed))
    assert retry.action == "UPDATED", retry.reason
    assert len(get_case(store, first.case_id).assessments) == 2


def test_competing_writer_and_conflicting_evidence_are_rejected(graph, truth):
    store, settings = graph
    ingest_directory(store, BASE)
    result = investigate(store, truth["scenarios"]["pattern_a"]["seed_refund_id"])
    before = image(store)
    with store.import_lock(), GraphStore(settings) as other:
        action = apply_case_policy(other, result)
        assert action.action == "NOT_CREATED_INCOMPLETE" and "lease" in action.reason
    assert image(store) == before
    bad = result.triggered_rules[0].evidence.model_copy(update={"contribution": 1.0})
    store.write("CREATE (e:Evidence) SET e = $props", {"props": bad.model_dump()})
    before = image(store)
    action = apply_case_policy(store, result)
    assert action.action == "NOT_CREATED_INCOMPLETE" and "conflicting" in action.reason
    assert image(store) == before


def test_list_display_caps_and_error_case_cli_actions(graph, truth):
    store, settings = graph
    ingest_directory(store, BASE)
    for name in ("pattern_a", "pattern_b"):
        assert (
            apply_case_policy(
                store, investigate(store, truth["scenarios"][name]["seed_refund_id"])
            ).action
            == "CREATED"
        )
    limited = list_cases(store, limit=1)
    assert len(limited.cases) == 1 and limited.truncated
    runner = CliRunner()
    seed = truth["scenarios"]["pattern_a"]["seed_refund_id"]
    text = runner.invoke(
        app,
        [
            "investigate",
            "--refund-id",
            seed,
            "--graph",
            settings.graph_name,
            "--display-paths",
            "0",
            "--create-case",
        ],
    )
    assert text.exit_code == 0 and "Priority: HIGH" in text.output and "Omitted" in text.output
    assert "EXISTING_UNCHANGED" in text.output
    incomplete = runner.invoke(
        app,
        [
            "investigate",
            "--refund-id",
            seed,
            "--graph",
            settings.graph_name,
            "--max-accounts",
            "1",
            "--create-case",
            "--format",
            "json",
        ],
    )
    assert incomplete.exit_code == 3
    assert json.loads(incomplete.output)["case_action"]["action"] == "NOT_CREATED_INCOMPLETE"
    missing = runner.invoke(
        app,
        [
            "investigate",
            "--refund-id",
            "ref_missing",
            "--graph",
            settings.graph_name,
            "--create-case",
            "--format",
            "json",
        ],
    )
    assert missing.exit_code == 1
    assert json.loads(missing.output)["case_action"]["action"] == "NOT_CREATED_INCOMPLETE"


def test_schema_adds_indexes_and_preserves_existing_records(graph):
    store, _ = graph
    old_indexes = [
        index
        for index in INDEXES
        if index[0] not in {"Assessment", "AgentRun"}
        and index != ("Investigation", "group_key")
        and index
        not in {
            ("Review", "reviewer_id"),
            ("Review", "dataset_id"),
            ("Review", "publication_status"),
        }
    ]
    assert len(old_indexes) == 20
    for label, field in old_indexes:
        store.write(f"CREATE INDEX FOR (n:{label}) ON (n.{field})")
    ingest_directory(store, BASE)
    before = image(store)
    initialized = initialize_schema(store)
    assert initialized.created_indexes == 10 and initialized.existing_indexes == 20
    assert initialize_schema(store).created_indexes == 0
    assert image(store) == before


def test_medium_case_creation_and_open_case_falling_low_do_not_close(graph, truth):
    store, _ = graph
    ingest_directory(store, BASE)
    seed = truth["scenarios"]["pattern_a"]["seed_refund_id"]
    medium = investigate(
        store, seed, config=InvestigationConfig(scoring=ScoringConfig(r3_points=0))
    )
    assert medium.total_score == 65 and medium.priority == "MEDIUM"
    created = apply_case_policy(store, medium)
    assert created.action == "CREATED", created.reason
    low = investigate(
        store, seed, config=InvestigationConfig(scoring=ScoringConfig(min_accounts=100))
    )
    updated = apply_case_policy(store, low)
    assert updated.action == "UPDATED", updated.reason
    saved = get_case(store, created.case_id)
    assert saved.investigation.status == "OPEN" and saved.investigation.priority == "LOW"
    assert saved.investigation.score == 0 and len(saved.assessments) == 2


def test_unexpected_persistence_error_is_sanitized_and_retryable(graph, truth, monkeypatch):
    store, _ = graph
    ingest_directory(store, BASE)
    result = investigate(store, truth["scenarios"]["pattern_a"]["seed_refund_id"])
    real_write = store.write

    def fail(query, params=None, **kwargs):
        if query == q.EVIDENCE:
            raise RuntimeError("private payload must not appear in output")
        return real_write(query, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", fail)
        failed = apply_case_policy(store, result)
    assert failed.action == "NOT_CREATED_INCOMPLETE" and "private payload" not in failed.reason
    assert not list_cases(store).cases
    assert apply_case_policy(store, result).action == "CREATED"
