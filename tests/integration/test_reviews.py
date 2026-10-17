"""Human memory uses real graph snapshots; scenario labels remain test-only."""

import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from ringlens import case_queries as cq
from ringlens import review_queries as q
from ringlens.cases import apply_case_policy, get_case
from ringlens.cli import app
from ringlens.config import Settings
from ringlens.graph_store import GraphConnectionError, GraphStore
from ringlens.ingest import ingest_directory
from ringlens.investigation_models import InvestigationConfig, ScoringConfig
from ringlens.investigator import investigate
from ringlens.review_models import ReviewRequest
from ringlens.reviews import compare_evidence, evidence_facts, record_review, review_history
from ringlens.schema import INDEXES, initialize_schema

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
BASE, APPEND = ROOT / "data/demo/base", ROOT / "data/demo/new_evidence"
HOUSE = "ref_4d44e5cc3e0a8dae77fd"
PATTERN = "ref_2b42ec18e21a70bdfa15"


@pytest.fixture
def graph():
    name = f"ringlens_test_reviews_{uuid4().hex}"
    settings = Settings(graph_name=name)
    with GraphStore(settings) as store:
        assert not store.graph_exists(), "Refusing an existing graph"
        try:
            ingest_directory(store, BASE)
            yield store, settings
        finally:
            if name in store._db.list_graphs():
                store._db.select_graph(name).delete()


def request(refund=HOUSE, **kwargs):
    return ReviewRequest(
        refund_id=refund,
        reviewer_id="human_demo_reviewer",
        outcome="BENIGN_EXPLANATION",
        explanation="Shared device and shipping location were explained by the reviewer.",
        **kwargs,
    )


def count(store, label):
    return store.read(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]


def test_low_review_without_case_restart_then_new_evidence(graph):
    store, settings = graph
    result = investigate(store, HOUSE)
    assert result.total_score == 35
    review = record_review(store, request(rule_ids=["R1", "R5"]))
    assert review.publication_status == "READY"
    assert count(store, "Investigation") == 0 and count(store, "Assessment") == 1
    assert record_review(store, request(rule_ids=["R1", "R5"])) == review
    context = compare_evidence(store, investigate(store, HOUSE))
    assert len(context.benign_covered) == 2 and not context.material_new
    assert context.recommendation == "ALREADY_REVIEWED"
    assert apply_case_policy(store, result).action == "ALREADY_REVIEWED"
    with GraphStore(settings) as restarted:
        assert review_history(restarted) == [review]
    process = subprocess.run(
        [str(ROOT / ".venv/bin/ringlens"), "reviews", "history", "--graph", settings.graph_name],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    assert review.id in process.stdout
    ingest_directory(store, APPEND)
    raised = investigate(store, HOUSE)
    after = compare_evidence(store, raised)
    assert raised.total_score == 100 and len(after.benign_covered) == 2
    new_rules = {f.rule_id for f in after.facts if f.fingerprint in after.material_new}
    assert new_rules == {"R2", "R3", "R4"}
    created = apply_case_policy(store, raised)
    assert created.action == "CREATED", created.reason
    assert apply_case_policy(store, raised).action == "EXISTING_UNCHANGED"
    assert review_history(store) == [review]
    assert compare_evidence(store, raised).material_new == after.material_new


def test_scoped_review_does_not_clear_other_rules_and_corrections_append(graph):
    store, _ = graph
    result = investigate(store, PATTERN)
    benign = record_review(store, request(PATTERN, rule_ids=["R1"]))
    context = compare_evidence(store, result)
    assert len(context.benign_covered) == 1 and len(context.unexplained) > 0
    assert apply_case_policy(store, result).action == "CREATED"
    correction = request(PATTERN, rule_ids=["R1"], supersedes=benign.id).model_copy(
        update={"outcome": "NEEDS_MORE_INFORMATION", "explanation": "Please verify device custody."}
    )
    changed = record_review(store, correction)
    assert changed.supersedes == benign.id
    assert record_review(store, correction) == changed
    context = compare_evidence(store, result)
    assert [r.id for r in context.reviews] == [changed.id]
    assert not context.benign_covered and context.considered
    assert len(review_history(store)) == 2
    assert len(store.read("MATCH (:Review)-[:SUPERSEDES]->(:Review) RETURN 1").result_set) == 1
    with pytest.raises(ValueError, match="latest replacement"):
        record_review(
            store, correction.model_copy(update={"explanation": "Conflicting correction"})
        )


def test_closed_case_reopens_only_on_material_qualifying_evidence(graph):
    store, _ = graph
    # Controlled policy fixture: base household qualifies under an explicit config.
    config = InvestigationConfig(scoring=ScoringConfig(r1_points=40, r3_points=10, r4_points=0))
    result = investigate(store, HOUSE, config=config)
    created = apply_case_policy(store, result)
    assert created.action == "CREATED" and result.priority == "MEDIUM"
    review = record_review(store, request(case_id=created.case_id, case_status="CLOSED"))
    assert get_case(store, created.case_id).investigation.status == "CLOSED"
    assert apply_case_policy(store, investigate(store, HOUSE)).action == "ALREADY_REVIEWED"
    ingest_directory(store, APPEND)
    raised = investigate(store, HOUSE)
    assert compare_evidence(store, raised).recommendation == "REOPEN"
    reopened = apply_case_policy(store, raised)
    assert reopened.action == "REOPENED", reopened.reason
    assert reopened.case_id == created.case_id
    saved = get_case(store, created.case_id)
    assert saved.investigation.status == "REOPENED"
    assert len(saved.assessments) == 2 and review.id in {r.id for r in saved.reviews}
    assert apply_case_policy(store, raised).action == "EXISTING_UNCHANGED"
    assert count(store, "Investigation") == 1


def test_clock_or_unrelated_revision_is_not_new_fact(graph):
    store, _ = graph
    review = record_review(store, request(PATTERN))
    original = investigate(store, PATTERN)
    earlier = investigate(store, PATTERN, as_of=original.assessment_as_of - 60)
    assert [f.fingerprint for f in evidence_facts(original)] == [
        f.fingerprint for f in evidence_facts(earlier)
    ]
    ingest_directory(store, APPEND)
    current = investigate(store, PATTERN)
    assert current.input_revision != original.input_revision
    context = compare_evidence(store, current)
    assert context.reviews == [review] and not context.material_new and not context.unexplained
    assert apply_case_policy(store, current).action == "ALREADY_REVIEWED"
    assert count(store, "Investigation") == 0


def test_review_failure_is_not_published_and_retry_finishes(graph, monkeypatch):
    store, _ = graph
    original = store.write

    def fail(query, *args, **kwargs):
        if query == q.FINISH_REVIEW:
            raise GraphConnectionError("Controlled disconnected write")
        return original(query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", fail)
        with pytest.raises(GraphConnectionError):
            record_review(store, request())
    assert not review_history(store)
    assert not compare_evidence(store, investigate(store, HOUSE)).benign_covered
    review = record_review(store, request())
    assert review.publication_status == "READY" and count(store, "Review") == 1


def test_invalid_scope_and_missing_refund_do_not_write(graph):
    store, _ = graph
    for req in [request(rule_ids=["R2"]), request(refund="ref_missing")]:
        with pytest.raises(ValueError):
            record_review(store, req)
    assert count(store, "Review") == count(store, "Assessment") == 0


def test_expired_review_does_not_clear_later_assessment(graph):
    store, _ = graph
    horizon = investigate(store, HOUSE).assessment_as_of
    record_review(store, request(valid_until=horizon))
    ingest_directory(store, APPEND)
    context = compare_evidence(store, investigate(store, HOUSE))
    assert not context.reviews and not context.benign_covered


def test_review_snapshot_can_be_attached_to_new_case_without_duplicates(graph):
    store, _ = graph
    record_review(store, request(PATTERN, rule_ids=["R1"]))
    result = investigate(store, PATTERN)
    assert count(store, "Assessment") == 1
    action = apply_case_policy(store, result)
    assert action.action == "CREATED", action.reason
    assert count(store, "Assessment") == 1
    assert get_case(store, action.case_id).assessments[0].status == "READY"


def test_completed_review_snapshot_attachment_retry_preserves_visible_history(graph, monkeypatch):
    store, _ = graph
    first = apply_case_policy(store, investigate(store, PATTERN))
    ingest_directory(store, APPEND)
    record_review(store, request(PATTERN, rule_ids=["R1"]))
    current = investigate(store, PATTERN)
    original = store.write

    def fail(query, *args, **kwargs):
        if query == cq.PUBLISH_WITH_EVIDENCE:
            raise GraphConnectionError("Controlled publish failure")
        return original(query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", fail)
        interrupted = apply_case_policy(store, current)
    assert interrupted.action == "NOT_CREATED_INCOMPLETE"
    detail = get_case(store, first.case_id)
    assert len(detail.assessments) == 1 and len(detail.pending_assessment_ids) == 1
    assert apply_case_policy(store, current).action == "UPDATED"
    assert len(get_case(store, first.case_id).assessments) == 2


def test_schema_upgrade_25_to_30_preserves_source_records(graph):
    store, _ = graph
    extra = {
        ("Review", "reviewer_id"),
        ("Review", "dataset_id"),
        ("Review", "publication_status"),
        ("AgentRun", "id"),
        ("AgentRun", "started_at"),
    }
    for label, field in INDEXES:
        if (label, field) not in extra:
            store.write(f"CREATE INDEX FOR (n:{label}) ON (n.{field})")
    before = store.read("MATCH (n) RETURN id(n), properties(n) ORDER BY id(n)").result_set
    initialized = initialize_schema(store)
    assert initialized.created_indexes == 5 and initialized.existing_indexes == 25
    assert initialize_schema(store).created_indexes == 0
    assert store.read("MATCH (n) RETURN id(n), properties(n) ORDER BY id(n)").result_set == before


def test_reviewer_cli_records_and_reads_scope(graph):
    store, settings = graph
    runner = CliRunner()
    args = [
        "reviews",
        "record",
        "--refund-id",
        HOUSE,
        "--reviewer-id",
        "cli-human",
        "--outcome",
        "BENIGN_EXPLANATION",
        "--explanation",
        "Shared resources explained.",
        "--rule",
        "R1",
        "--rule",
        "R5",
        "--graph",
        settings.graph_name,
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 0, first.output
    review = json.loads(first.output)
    assert review["publication_status"] == "READY" and review["reviewer_id"] == "cli-human"
    assert json.loads(runner.invoke(app, args).output)["id"] == review["id"]
    history = runner.invoke(
        app, ["reviews", "history", "--refund-id", HOUSE, "--graph", settings.graph_name]
    )
    assert history.exit_code == 0 and json.loads(history.output)["reviews"][0]["id"] == review["id"]
    assert count(store, "Investigation") == 0 and count(store, "Review") == 1
