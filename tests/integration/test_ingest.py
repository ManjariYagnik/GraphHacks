"""Real FalkorDB imports. Each test owns and deletes only its UUID-named graph."""

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from falkordb.path import Path as GraphPath
from typer.testing import CliRunner

from ringlens import import_queries as q
from ringlens.cli import app
from ringlens.config import Settings
from ringlens.dataset_io import batch_bytes, make_batch, read_batch
from ringlens.graph_store import GraphConnectionError, GraphImportBusyError, GraphStore
from ringlens.ingest import IngestionError, ingest_batch, ingest_directory
from ringlens.models import Evidence, Investigation, Review
from ringlens.schema import initialize_schema
from ringlens.validation import RecordValidationError, SourceRecords, source_hash
from ringlens.verify import (
    graph_stats,
    path_json,
    read_history,
    read_state,
    sample_paths,
    verify_graph,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data/demo/base"
APPEND = ROOT / "data/demo/new_evidence"
COUNTS = {"accounts": 50, "devices": 42, "addresses": 46, "orders": 250, "refunds": 42}


@pytest.fixture
def owned_graph():
    name = f"ringlens_test_ingest_{uuid4().hex}"
    settings = Settings(graph_name=name)
    with GraphStore(settings) as store:
        assert name not in store._db.list_graphs(), "Refusing an existing graph"
        try:
            yield store, settings
        finally:
            if name in store._db.list_graphs():
                store._db.select_graph(name).delete()
            assert name not in store._db.list_graphs()


def image(store):
    if not store.graph_exists():
        return None
    return (
        store.read("MATCH (n) RETURN id(n), labels(n), properties(n) ORDER BY id(n)").result_set,
        store.read(
            "MATCH (a)-[r]->(b) RETURN id(r), id(a), type(r), id(b), properties(r) ORDER BY id(r)"
        ).result_set,
    )


def save_batch(directory, batch):
    directory.mkdir()
    for filename, payload in batch_bytes(batch).items():
        (directory / filename).write_bytes(payload)


def add_case_fixture(store, account_id, as_of):
    investigation = Investigation(
        id="inv_fixture",
        status="OPEN",
        priority="LOW",
        score=0,
        rule_version="fixture_v1",
        assessment_as_of=as_of,
        created_at=as_of,
        updated_at=as_of,
    )
    evidence = Evidence(
        id="ev_fixture",
        rule_id="fixture_only",
        rule_version="fixture_v1",
        window_start=as_of - 30 * 86400,
        window_end=as_of,
        contribution=0,
        source_ids=[account_id],
        details_json='{"fixture":true}',
    )
    review = Review(
        id="rev_fixture",
        outcome="NEEDS_MORE_INFORMATION",
        explanation="Synthetic fixture for preservation checks",
        reviewed_at=as_of,
    )
    store.write(
        """
        CREATE (i:Investigation), (e:Evidence), (r:Review)
        SET i = $investigation, e = $evidence, r = $review
        WITH i, e, r MATCH (a:Account {id: $account_id})
        CREATE (i)-[:INCLUDES]->(a), (i)-[:HAS_EVIDENCE]->(e),
               (i)-[:HAS_REVIEW]->(r), (r)-[:ABOUT]->(e)
    """,
        {
            "account_id": account_id,
            "investigation": investigation.model_dump(),
            "evidence": evidence.model_dump(),
            "review": review.model_dump(),
        },
    )


def case_image(store):
    params = {"ids": ["inv_fixture", "ev_fixture", "rev_fixture"]}
    return (
        store.read(
            "MATCH (n) WHERE n.id IN $ids RETURN id(n), properties(n) ORDER BY id(n)", params
        ).result_set,
        store.read(
            "MATCH (a)-[r]->(b) WHERE a.id IN $ids OR b.id IN $ids "
            "RETURN id(r), id(a), type(r), id(b) ORDER BY id(r)",
            params,
        ).result_set,
    )


def test_full_base_append_replay_case_preservation_and_genuine_paths(owned_graph, monkeypatch):
    store, _ = owned_graph
    original_read = Path.read_bytes

    def no_evaluation(self):
        if "evaluation" in self.parts:
            raise AssertionError("ingestion must never read evaluation")
        return original_read(self)

    monkeypatch.setattr(Path, "read_bytes", no_evaluation)
    dry = ingest_directory(store, BASE, dry_run=True)
    assert dry.status == "DRY_RUN" and dry.counts == COUNTS
    assert not store.graph_exists()  # No metadata, source nodes, edges, or indexes.
    initialize_schema(store)
    base_result = ingest_directory(store, BASE)
    assert base_result.status == "READY" and base_result.counts == COUNTS
    assert base_result.verification.dataset_status == "READY"
    assert verify_graph(store).ok
    before = image(store)
    assert ingest_directory(store, BASE).status == "NO_OP"
    assert image(store) == before
    base_batch = read_batch(BASE)
    add_case_fixture(store, base_batch.records.accounts[0].id, base_batch.manifest.as_of)
    case_before = case_image(store)
    before = image(store)
    dry_append = ingest_directory(store, APPEND, dry_run=True)
    assert dry_append.counts == {**COUNTS, "refunds": 48}
    assert image(store) == before
    appended = ingest_directory(store, APPEND)
    assert appended.status == "READY" and appended.counts == {**COUNTS, "refunds": 48}
    assert appended.as_of == read_batch(APPEND).manifest.as_of
    assert appended.verification.dataset_status == "READY"
    assert appended.verification.as_of == appended.as_of
    assert case_image(store) == case_before
    before = image(store)
    assert ingest_directory(store, APPEND).status == "NO_OP"
    assert ingest_directory(store, BASE).status == "NO_OP"  # Never rolls back append horizon.
    assert image(store) == before
    report = verify_graph(store)
    assert report.ok and report.counts == {**COUNTS, "refunds": 48}
    assert report.relationship_counts == {
        "PLACED": 250,
        "USED_DEVICE": 250,
        "SHIPPED_TO": 250,
        "REQUESTED_REFUND": 48,
        "FOR_ORDER": 48,
        "HAS_BATCH": 2,
    }
    assert len(report.batches) == 2 and all(batch["status"] == "READY" for batch in report.batches)
    stats = graph_stats(store)
    assert stats["labels"]["Investigation"] == {"nodes": 1, "distinct_ids": 1}
    assert stats["labels"]["Evidence"]["nodes"] == stats["labels"]["Review"]["nodes"] == 1

    # Choose a seed from actual source topology, not planted scenario membership.
    orders = {order.id: order for order in base_batch.records.orders}
    users = {}
    for order in orders.values():
        users.setdefault(order.device_id, set()).add(order.account_id)
    seed = next(
        refund
        for refund in base_batch.records.refunds
        if len(users[orders[refund.order_id].device_id]) >= 3
    )
    paths = sample_paths(store, refund_id=seed.id, limit=20)
    assert len(paths["refund_paths"]) == 1 and paths["peer_paths"]
    assert paths["unique_peer_count"] == len(users[orders[seed.order_id].device_id]) - 1
    for row in paths["refund_paths"]:
        assert all(isinstance(path, GraphPath) for path in row)
        assert [path.edge_count() for path in row] == [2, 1, 1]
        assert path_json(row[0])["nodes"][0]["id"] == seed.id
    for row in paths["peer_paths"]:
        assert isinstance(row[0], GraphPath) and row[0].edge_count() == 4
    assert (
        len(paths["peer_paths"]) > paths["unique_peer_count"]
    )  # Paths retained; counts deduplicated.


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [
        ("orders", "account_id", "acct_missing"),
        ("orders", "device_id", "dev_missing"),
        ("orders", "address_id", "addr_missing"),
        ("refunds", "order_id", "ord_missing"),
    ],
)
def test_missing_reference_rejected_without_any_writes(owned_graph, tmp_path, kind, field, value):
    store, _ = owned_graph
    batch = read_batch(BASE)
    records = batch.records.model_dump()
    records[kind][0][field] = value
    bad = make_batch(
        SourceRecords.model_validate(records), seed=42, as_of=batch.manifest.as_of, mode="base"
    )
    directory = tmp_path / "bad"
    save_batch(directory, bad)
    with pytest.raises(RecordValidationError, match="orphan"):
        ingest_directory(store, directory)
    assert not store.graph_exists()


def test_bad_checksum_and_dry_run_append_without_base_create_nothing(owned_graph, tmp_path):
    store, _ = owned_graph
    directory = tmp_path / "bad"
    save_batch(directory, read_batch(BASE))
    with (directory / "accounts.jsonl").open("ab") as file:
        file.write(b"\n")
    with pytest.raises(ValueError, match="SHA-256"):
        ingest_directory(store, directory)
    with pytest.raises(IngestionError, match="existing successfully imported base"):
        ingest_directory(store, APPEND, dry_run=True)
    assert not store.graph_exists()


def test_immutable_conflict_rejected_before_writes(owned_graph):
    store, _ = owned_graph
    ingest_directory(store, BASE)
    before = image(store)
    base, append = read_batch(BASE), read_batch(APPEND)
    changed = base.records.accounts[0].model_copy(
        update={"created_at": base.records.accounts[0].created_at - 1}
    )
    bad = make_batch(
        SourceRecords(accounts=[changed], refunds=append.records.refunds),
        seed=42,
        as_of=append.manifest.as_of,
        mode="append",
    )
    with pytest.raises(IngestionError, match="immutable"):
        ingest_batch(store, bad)
    assert image(store) == before


def test_nonadvancing_append_and_second_refund_rejected_before_writes(owned_graph):
    store, _ = owned_graph
    ingest_directory(store, BASE)
    before = image(store)
    base, append = read_batch(BASE), read_batch(APPEND)
    nonadvancing = make_batch(append.records, seed=42, as_of=base.manifest.as_of, mode="append")
    with pytest.raises(IngestionError, match="advance"):
        ingest_batch(store, nonadvancing)
    assert image(store) == before
    refunds = list(append.records.refunds)
    previous = next(row for row in base.records.refunds if row.account_id == refunds[0].account_id)
    refunds[0] = refunds[0].model_copy(update={"order_id": previous.order_id, "amount_minor": 1})
    bad = make_batch(
        SourceRecords(refunds=refunds), seed=42, as_of=append.manifest.as_of, mode="append"
    )
    with pytest.raises(RecordValidationError, match="multiple refunds"):
        ingest_batch(store, bad)
    assert image(store) == before


@pytest.mark.parametrize("fail_at", [3, 13])
def test_mid_import_failure_is_failed_and_same_batch_retry_completes(
    owned_graph, monkeypatch, fail_at
):
    store, _ = owned_graph
    real_write = store.write
    calls = 0

    def failing_write(query, params=None, **kwargs):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise RuntimeError("injected payload must not appear in metadata errors")
        return real_write(query, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", failing_write)
        with pytest.raises(IngestionError, match="failed before completion"):
            ingest_directory(store, BASE)
    dataset, history = read_history(read_state(store))
    assert dataset.status == "FAILED" and history[0].batch.status == "FAILED"
    assert "RuntimeError" in history[0].batch.error
    assert "injected payload" not in history[0].batch.error
    assert not verify_graph(store).ok
    assert ingest_directory(store, BASE).status == "READY"
    assert verify_graph(store).ok and verify_graph(store).counts == COUNTS
    assert len(read_history(read_state(store))[1]) == 1


def test_crash_loading_blocks_different_batch_but_can_resume_same_batch(owned_graph, monkeypatch):
    store, _ = owned_graph
    real_write = store.write
    calls = 0

    class SimulatedCrash(BaseException):
        pass

    def crash(query, params=None, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise SimulatedCrash
        return real_write(query, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", crash)
        with pytest.raises(SimulatedCrash):
            ingest_directory(store, BASE)
    dataset, history = read_history(read_state(store))
    assert dataset.status == "INGESTING" and history[0].batch.status == "LOADING"
    before = image(store)
    with pytest.raises(IngestionError, match="different import is unfinished"):
        ingest_directory(store, APPEND)
    assert image(store) == before
    assert ingest_directory(store, BASE).status == "READY"


def test_import_lease_rejects_competing_writer(owned_graph):
    store, settings = owned_graph
    with store.import_lock(), GraphStore(settings) as other:
        with pytest.raises(GraphImportBusyError, match="lease"):
            ingest_directory(other, BASE)
    assert not store.graph_exists()


@pytest.mark.parametrize("pretend_success", [False, True])
def test_missing_match_is_not_silently_accepted(owned_graph, monkeypatch, pretend_success):
    store, _ = owned_graph
    real_write = store.write
    skipped = False

    def missing_match(query, params=None, **kwargs):
        nonlocal skipped
        if query == q.WRITE_EDGES["USED_DEVICE"] and not skipped:
            skipped = True
            return SimpleNamespace(result_set=[[len(params["rows"]) if pretend_success else 0]])
        return real_write(query, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", missing_match)
        with pytest.raises(
            IngestionError,
            match="Graph verification failed" if pretend_success else "every expected endpoint",
        ):
            ingest_directory(store, BASE)
    assert read_history(read_state(store))[0].status == "FAILED"
    assert ingest_directory(store, BASE).status == "READY"


def test_ready_publish_ack_loss_preserves_success_and_retry_is_no_op(owned_graph, monkeypatch):
    store, _ = owned_graph
    real_write = store.write

    def lost_ack(query, params=None, **kwargs):
        result = real_write(query, params, **kwargs)
        if query == q.FINISH_BATCH:
            raise GraphConnectionError("Synthetic acknowledgement loss")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", lost_ack)
        with pytest.raises(GraphConnectionError):
            ingest_directory(store, BASE)
    assert verify_graph(store).ok
    assert read_history(read_state(store))[0].status == "READY"
    assert ingest_directory(store, BASE).status == "NO_OP"


def test_failed_append_keeps_previous_horizon_until_retry_succeeds(owned_graph, monkeypatch):
    store, _ = owned_graph
    ingest_directory(store, BASE)
    base_horizon = read_history(read_state(store))[0].as_of
    real_write = store.write
    failed = False

    def fail(query, params=None, **kwargs):
        nonlocal failed
        if query == q.WRITE_EDGES["FOR_ORDER"] and not failed:
            failed = True
            raise RuntimeError("injected")
        return real_write(query, params, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "write", fail)
        with pytest.raises(IngestionError):
            ingest_directory(store, APPEND)
    dataset, history = read_history(read_state(store))
    assert dataset.as_of == base_horizon and dataset.status == "FAILED"
    assert len(history) == 2
    assert ingest_directory(store, APPEND).status == "READY"
    assert read_history(read_state(store))[0].as_of == read_batch(APPEND).manifest.as_of


def test_different_dataset_assignment_is_preserved(owned_graph):
    store, _ = owned_graph
    store.write(
        "CREATE (:Dataset {id: $id, schema_version: $version, as_of: $as_of, status: $status})",
        {"id": "other_dataset", "version": "1.0", "as_of": 100, "status": "READY"},
    )
    before = image(store)
    with pytest.raises(IngestionError, match="different dataset_id"):
        ingest_directory(store, BASE)
    assert image(store) == before


@pytest.mark.parametrize(
    "corruption", ["duplicate_node", "duplicate_edge", "changed_content", "horizon"]
)
def test_verification_detects_graph_corruption(owned_graph, corruption):
    store, _ = owned_graph
    ingest_directory(store, BASE)
    base = read_batch(BASE)
    if corruption == "duplicate_node":
        store.write(
            "MATCH (a:Account {id: $id}) CREATE (b:Account) SET b = properties(a)",
            {"id": base.records.accounts[0].id},
        )
    elif corruption == "duplicate_edge":
        store.write(
            "MATCH (a:Account)-[:PLACED]->(o:Order {id: $id}) CREATE (a)-[:PLACED]->(o)",
            {"id": base.records.orders[0].id},
        )
    elif corruption == "changed_content":
        account = base.records.accounts[0]
        changed = account.model_copy(update={"created_at": account.created_at - 1})
        store.write(
            "MATCH (a:Account {id: $id}) SET a.created_at = $at, a.source_hash = $hash",
            {"id": account.id, "at": changed.created_at, "hash": source_hash(changed)},
        )
    else:
        store.write("MATCH (d:Dataset) SET d.as_of = $as_of", {"as_of": base.manifest.as_of + 1})
    report = verify_graph(store)
    assert not report.ok and report.issues
    if corruption == "duplicate_node":
        assert report.node_counts["accounts"] == 51 and report.counts["accounts"] == 50


def test_cli_explicit_graph_and_structured_reports(owned_graph):
    store, settings = owned_graph
    runner = CliRunner()
    graph = ["--graph", settings.graph_name]
    result = runner.invoke(app, ["ingest", "--input", str(BASE), "--dry-run", *graph])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "DRY_RUN"
    assert not store.graph_exists()
    result = runner.invoke(app, ["graph", "verify", *graph])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False
    result = runner.invoke(app, ["ingest", "--input", str(BASE), *graph])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "READY"
    result = runner.invoke(app, ["graph", "verify", *graph])
    assert result.exit_code == 0 and json.loads(result.output)["ok"] is True
    result = runner.invoke(app, ["graph", "stats", *graph])
    assert (
        result.exit_code == 0
        and json.loads(result.output)["labels"]["Refund"]["distinct_ids"] == 42
    )
    result = runner.invoke(app, ["graph", "paths", *graph])
    assert result.exit_code == 0 and json.loads(result.output)["refund_paths"]
