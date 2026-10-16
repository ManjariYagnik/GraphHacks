"""Real FalkorDB acceptance/evaluation checks. Only this test reads ground truth."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from ringlens import import_queries as metadata_q
from ringlens import investigation_queries as q
from ringlens.cli import app
from ringlens.config import Settings
from ringlens.dataset_io import make_batch, read_batch
from ringlens.graph_store import GraphStore, GraphTimeoutError
from ringlens.ingest import ingest_batch, ingest_directory
from ringlens.investigation_models import InvestigationConfig
from ringlens.investigator import (
    InvestigationDataUnavailable,
    InvestigationInputError,
    investigate,
)
from ringlens.models import Account, Address, Device, Order, Refund
from ringlens.validation import SourceRecords

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
BASE, APPEND = ROOT / "data/demo/base", ROOT / "data/demo/new_evidence"


@contextmanager
def disposable_graph():
    name = f"ringlens_test_investigate_{uuid4().hex}"
    with GraphStore(Settings(graph_name=name)) as store:
        assert not store.graph_exists(), "Refusing to use an existing graph"
        try:
            yield store, name
        finally:
            if name in store._db.list_graphs():
                store._db.select_graph(name).delete()
            assert name not in store._db.list_graphs()


@pytest.fixture
def graph():
    with disposable_graph() as value:
        yield value


@pytest.fixture
def truth():
    # Evaluation inputs are available to the acceptance test, never the investigator.
    return json.loads((ROOT / "evaluation/demo_ground_truth.json").read_text())


def source_image(store):
    return (
        store.read("MATCH (n) RETURN id(n), labels(n), properties(n) ORDER BY id(n)").result_set,
        store.read(
            "MATCH (a)-[r]->(b) RETURN id(r), id(a), type(r), id(b) ORDER BY id(r)"
        ).result_set,
    )


@pytest.mark.parametrize(
    ("scenario", "score"),
    [("pattern_a", 90), ("pattern_b", 90), ("household", 35), ("ordinary", 0)],
)
def test_base_scenarios_genuine_paths_determinism_and_read_only(
    graph, truth, monkeypatch, scenario, score
):
    store, _ = graph
    ingest_directory(store, BASE)
    expected = truth["scenarios"][scenario]
    before = source_image(store)
    with monkeypatch.context() as patch:

        def forbidden(*args, **kwargs):
            raise AssertionError("Investigation may not write or read local input files")

        patch.setattr(store, "write", forbidden)
        patch.setattr(Path, "read_bytes", forbidden)
        patch.setattr(Path, "read_text", forbidden)
        result = investigate(store, expected["seed_refund_id"])
        again = investigate(store, expected["seed_refund_id"])
    assert result.completeness == "COMPLETE", result.errors
    assert result == again
    assert result.total_score == score
    assert result.priority == ("HIGH" if score >= 70 else "LOW")
    assert {c.id for c in result.candidates if c.depth <= 1} == set(
        expected["expected_accounts_after_expansion_1"]
    )
    assert {c.id for c in result.candidates} == set(expected["expected_accounts_after_expansion_2"])
    assert len(result.analysis.bursts) == expected["expected_base_burst_count"]
    assert result.assessment_as_of == truth["base_as_of"]
    assert result.input_revision and len(result.successful_batch_ids) == 1
    assert source_image(store) == before
    assert len({proof.id for proof in result.paths}) == len(result.paths)
    assert all(finding.path_ids and finding.query_ids for finding in result.triggered_rules)
    known = {proof.id for proof in result.paths}
    assert all(set(finding.path_ids) <= known for finding in result.triggered_rules)
    assert all(finding.evidence.details_json.startswith("{") for finding in result.triggered_rules)
    # Validate returned physical paths against the actual database, not fabricated triples.
    for proof in result.paths:
        for edge in proof.path["edges"]:
            found = store.read(
                "MATCH (a)-[r]->(b) WHERE id(r) = $edge RETURN id(a), type(r), id(b)",
                {"edge": edge["graph_id"]},
            ).result_set
            assert found == [[edge["source_graph_id"], edge["type"], edge["target_graph_id"]]]
    assert ingest_directory(store, BASE).status == "NO_OP"
    assert investigate(store, expected["seed_refund_id"]) == result


def test_household_append_changes_evidence_and_horizon_without_case_nodes(graph, truth):
    store, _ = graph
    expected = truth["scenarios"]["household"]
    seed = expected["seed_refund_id"]
    ingest_directory(store, BASE)
    before = investigate(store, seed)
    ingest_directory(store, APPEND)
    after = investigate(store, seed)
    assert before.total_score == 35 and before.priority == "LOW"
    assert after.completeness == "COMPLETE", after.errors
    assert after.total_score == 100 and after.priority == "HIGH"
    assert [f.evidence.rule_id for f in after.triggered_rules] == ["R1", "R2", "R3", "R4", "R5"]
    assert len(after.analysis.bursts) == 2
    assert all((rate.numerator, rate.denominator) == (3, 5) for rate in after.analysis.rates)
    assert before.input_revision != after.input_revision
    assert len(after.successful_batch_ids) == 2 and after.assessment_as_of == truth["append_as_of"]
    assert ingest_directory(store, APPEND).status == "NO_OP"
    assert investigate(store, seed) == after
    historical = investigate(store, seed, as_of=truth["base_as_of"])
    assert historical.total_score == before.total_score and historical.analysis == before.analysis
    assert historical.triggered_rules == before.triggered_rules
    assert historical.input_revision == after.input_revision
    assert not store.read(
        "MATCH (n) WHERE n:Investigation OR n:Evidence OR n:Review RETURN n"
    ).result_set


def test_duplicate_returned_paths_do_not_inflate_signals_or_evidence(graph, truth, monkeypatch):
    store, _ = graph
    ingest_directory(store, BASE)
    seed = truth["scenarios"]["pattern_a"]["seed_refund_id"]
    expected = investigate(store, seed)
    real_read = store.read

    def duplicated(query, params=None, **kwargs):
        result = real_read(query, params, **kwargs)
        if query in (q.ORDERS, q.REFUNDS):
            return SimpleNamespace(result_set=result.result_set * 2)
        return result

    monkeypatch.setattr(store, "read", duplicated)
    assert investigate(store, seed) == expected


@pytest.mark.parametrize("bound", ["accounts", "evidence"])
def test_discovery_or_evidence_cap_is_incomplete_never_low(graph, truth, bound):
    store, _ = graph
    ingest_directory(store, BASE)
    seed = truth["scenarios"]["pattern_b"]["seed_refund_id"]
    config = (
        InvestigationConfig(max_accounts=2)
        if bound == "accounts"
        else InvestigationConfig(max_evidence=2)
    )
    result = investigate(store, seed, config=config)
    assert result.completeness == "INCOMPLETE" and result.errors
    assert result.total_score is result.priority is result.analysis is None
    assert result.candidates and result.paths and result.source_records.refunds
    assert len(result.paths) <= config.max_evidence


@pytest.mark.parametrize("failed_query", [q.DISCOVER_ACCOUNTS, q.ORDERS, q.REFUNDS, q.DEVICE_USERS])
def test_required_query_failure_keeps_partial_evidence_without_final_score(
    graph, truth, monkeypatch, failed_query
):
    store, _ = graph
    ingest_directory(store, BASE)
    real_read = store.read

    def fail(query, params=None, **kwargs):
        if query == failed_query:
            raise GraphTimeoutError("Controlled query deadline")
        return real_read(query, params, **kwargs)

    monkeypatch.setattr(store, "read", fail)
    result = investigate(store, truth["scenarios"]["pattern_a"]["seed_refund_id"])
    assert result.completeness == "INCOMPLETE" and result.total_score is result.priority is None
    assert result.errors[0].code == "QUERY_FAILED"
    assert result.paths and result.seed_account_id


def test_missing_seed_nonready_data_and_invalid_parameters_are_explicit(graph, truth):
    store, _ = graph
    seed = truth["scenarios"]["pattern_a"]["seed_refund_id"]
    with pytest.raises(InvestigationDataUnavailable, match="ingest"):
        investigate(store, seed)
    ingest_directory(store, BASE)
    with pytest.raises(InvestigationDataUnavailable, match="Refund does not exist"):
        investigate(store, "ref_missing")
    for as_of in (truth["base_as_of"] + 1, 1):
        with pytest.raises(InvestigationInputError):
            investigate(store, seed, as_of=as_of)
    with pytest.raises(InvestigationInputError):
        investigate(store, "not_a_refund")
    for status in ("INGESTING", "FAILED"):
        store.write("MATCH (d:Dataset) SET d.status = $status", {"status": status})
        with pytest.raises(InvestigationDataUnavailable, match="READY"):
            investigate(store, seed)


def test_metadata_changed_during_queries_invalidates_final_result(graph, truth, monkeypatch):
    store, _ = graph
    ingest_directory(store, BASE)
    real_read, calls = store.read, 0

    def changed(query, params=None, **kwargs):
        nonlocal calls
        if query.startswith(metadata_q.READ_DATASETS):
            calls += 1
            if calls == 2:
                store.write("MATCH (d:Dataset) SET d.status = 'INGESTING'")
        return real_read(query, params, **kwargs)

    monkeypatch.setattr(store, "read", changed)
    result = investigate(store, truth["scenarios"]["pattern_a"]["seed_refund_id"])
    assert result.completeness == "INCOMPLETE" and result.analysis is result.total_score is None
    assert result.errors[0].code == "DATA_CHANGED"


def test_inconsistent_seed_ownership_is_an_error_not_low(graph, truth):
    store, _ = graph
    ingest_directory(store, BASE)
    seed = truth["scenarios"]["pattern_a"]["seed_refund_id"]
    store.write(
        "MATCH (a:Account)-[r:REQUESTED_REFUND]->(refund:Refund {id: $seed}), "
        "(other:Account {id: $other}) DELETE r CREATE (other)-[:REQUESTED_REFUND]->(refund)",
        {"seed": seed, "other": truth["scenarios"]["household"]["account_ids"][0]},
    )
    result = investigate(store, seed)
    assert result.completeness == "INCOMPLETE" and result.total_score is result.priority is None
    assert result.errors[0].code == "INCONSISTENT_DATA"


def small_records(usages, *, placed_at=100000):
    """Independent valid fixture: usages is [(account number, device token, time)]."""
    account_ids = sorted({account for account, _, _ in usages})
    return SourceRecords(
        accounts=[Account(id=f"acct_{i}", created_at=1) for i in account_ids],
        devices=[Device(id=f"dev_{i}") for i in sorted({device for _, device, _ in usages})],
        addresses=[Address(id=f"addr_{i}", label=f"Synthetic address {i}") for i in account_ids],
        orders=[
            Order(
                id=f"ord_{i}",
                account_id=f"acct_{account}",
                device_id=f"dev_{device}",
                address_id=f"addr_{account}",
                placed_at=time if time is not None else placed_at,
                amount_minor=1000,
                currency="INR",
            )
            for i, (account, device, time) in enumerate(usages)
        ],
    )


def test_exact_window_boundaries_and_outside_events_control_discovery(graph):
    store, _ = graph
    start, end = 100000, 186400
    rows = small_records(
        [(0, "x", start), (1, "x", start), (2, "x", end), (3, "x", start - 1), (4, "x", end + 1)]
    )
    rows = rows.model_copy(
        update={
            "refunds": [
                Refund(
                    id="ref_seed",
                    account_id="acct_0",
                    order_id="ord_0",
                    requested_at=end,
                    amount_minor=1,
                    currency="INR",
                ),
                Refund(
                    id="ref_start",
                    account_id="acct_1",
                    order_id="ord_1",
                    requested_at=start,
                    amount_minor=1,
                    currency="INR",
                ),
                Refund(
                    id="ref_future",
                    account_id="acct_4",
                    order_id="ord_4",
                    requested_at=end + 1,
                    amount_minor=1,
                    currency="INR",
                ),
            ]
        }
    )
    ingest_batch(store, make_batch(rows, seed=1, as_of=end + 10, mode="base"))
    result = investigate(store, "ref_seed", as_of=end, config=InvestigationConfig(window_days=1))
    assert result.completeness == "COMPLETE", result.errors
    assert {c.id for c in result.candidates} == {"acct_0", "acct_1", "acct_2"}
    assert result.device_user_counts == {"dev_x": 3}
    assert {r.id for r in result.source_records.refunds} == {"ref_seed", "ref_start"}
    assert {o.placed_at for o in result.source_records.orders} == {start, end}
    at_start = investigate(store, "ref_start", as_of=end, config=InvestigationConfig(window_days=1))
    assert at_start.completeness == "COMPLETE" and at_start.total_score == result.total_score


def test_whole_graph_device_count_includes_accounts_beyond_two_expansions(graph):
    store, _ = graph
    usages = [(0, "a", 100000), (1, "a", 100000), (1, "b", 100000)]
    usages += [(i, "b", 100000) for i in (2, 3, 4)]
    usages += [(i, "c", 100000) for i in (2, 3, 4, 5, 6, 7)]
    rows = small_records(usages)
    rows = rows.model_copy(
        update={
            "refunds": [
                Refund(
                    id="ref_seed",
                    account_id="acct_0",
                    order_id="ord_0",
                    requested_at=100100,
                    amount_minor=1,
                    currency="INR",
                )
            ]
        }
    )
    ingest_batch(store, make_batch(rows, seed=1, as_of=100200, mode="base"))
    result = investigate(store, "ref_seed")
    assert result.completeness == "COMPLETE", result.errors
    assert {c.id for c in result.candidates} == {f"acct_{i}" for i in range(5)}
    assert result.device_user_counts["dev_c"] == 6
    r1 = result.analysis.rules[0]
    assert r1.details["eligible_device_ids"] == ["dev_b"]
    outside = next(device for device in r1.details["devices"] if device["device_id"] == "dev_c")
    assert len(outside["candidate_account_ids"]) == 3 and outside["total_users"] == 6


def test_controlled_device_change_changes_graph_discovery_with_identical_refunds(graph, truth):
    store, _ = graph
    original = read_batch(BASE)
    seed = truth["scenarios"]["pattern_a"]["seed_refund_id"]
    owner = next(row.account_id for row in original.records.refunds if row.id == seed)
    ingest_batch(store, original)
    before = investigate(store, seed)
    changed = original.records.model_copy(
        update={
            "devices": [*original.records.devices, Device(id="dev_controlled")],
            "orders": [
                order.model_copy(update={"device_id": "dev_controlled"})
                if order.account_id == owner
                else order
                for order in original.records.orders
            ],
        }
    )
    assert changed.refunds == original.records.refunds
    assert [(o.id, o.amount_minor, o.placed_at) for o in changed.orders] == [
        (o.id, o.amount_minor, o.placed_at) for o in original.records.orders
    ]
    with disposable_graph() as (other, _):
        ingest_batch(
            other, make_batch(changed, seed=42, as_of=original.manifest.as_of, mode="base")
        )
        after = investigate(other, seed)
    assert before.total_score == 90 and len(before.candidates) == 4
    assert after.completeness == "COMPLETE", after.errors
    assert after.total_score == 0 and [c.id for c in after.candidates] == [owner]
    assert before.triggered_rules and not after.triggered_rules


def test_investigation_cli_formats_graph_override_and_exit_codes(graph, truth):
    store, name = graph
    ingest_directory(store, BASE)
    runner = CliRunner()
    seed = truth["scenarios"]["pattern_b"]["seed_refund_id"]
    args = ["investigate", "--refund-id", seed, "--graph", name]
    output = runner.invoke(app, [*args, "--format", "json"])
    assert output.exit_code == 0, output.output
    assert json.loads(output.output)["total_score"] == 90
    output = runner.invoke(app, args)
    assert (
        output.exit_code == 0
        and "Priority: HIGH" in output.output
        and "USED_DEVICE" in output.output
    )
    output = runner.invoke(app, [*args, "--max-accounts", "2", "--format", "json"])
    assert output.exit_code == 3 and json.loads(output.output)["priority"] is None
    output = runner.invoke(app, [*args, "--as-of", "2026-10-18T10:00:00Z", "--format", "json"])
    assert output.exit_code == 2 and json.loads(output.output)["error"]["kind"] == "INVALID_INPUT"
    output = runner.invoke(
        app, ["investigate", "--refund-id", "ref_missing", "--graph", name, "--format", "json"]
    )
    assert (
        output.exit_code == 1 and json.loads(output.output)["error"]["kind"] == "DATA_UNAVAILABLE"
    )
