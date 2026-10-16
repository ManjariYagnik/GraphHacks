"""Offline input, dry-run, and CLI checks alongside the separate integration module."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from ringlens.cli import app
from ringlens.dataset_io import DatasetBatch, read_batch
from ringlens.graph_store import GraphConnectionError, GraphStore
from ringlens.ingest import ImportResult, IngestionError, ingest_batch
from ringlens.verify import VerificationReport, verify_graph

BASE = Path(__file__).resolve().parents[2] / "data/demo/base"


def test_dry_run_on_absent_graph_never_creates_state_or_acquires_lease():
    store = MagicMock(spec=GraphStore)
    store.graph_exists.return_value = False
    result = ingest_batch(store, read_batch(BASE), dry_run=True)
    assert result.status == "DRY_RUN"
    assert result.counts == {
        "accounts": 50,
        "devices": 42,
        "addresses": 46,
        "orders": 250,
        "refunds": 42,
    }
    store.read.assert_not_called()
    store.write.assert_not_called()
    store.import_lock.assert_not_called()


def test_programmatic_batch_cannot_bypass_manifest_content_validation():
    original = read_batch(BASE)
    changed = original.records.accounts[0].model_copy(
        update={"created_at": original.records.accounts[0].created_at - 1}
    )
    records = original.records.model_copy(
        update={"accounts": [changed, *original.records.accounts[1:]]}
    )
    store = MagicMock(spec=GraphStore)
    with pytest.raises(IngestionError, match="manifest"):
        ingest_batch(store, DatasetBatch(records, original.manifest))
    store.graph_exists.assert_not_called()
    store.write.assert_not_called()
    store.import_lock.assert_not_called()


@pytest.mark.parametrize("batch_size", [0, 251, True])
def test_invalid_batch_size_is_rejected_before_graph_access(batch_size):
    store = MagicMock(spec=GraphStore)
    with pytest.raises(ValueError, match="batch_size"):
        ingest_batch(store, read_batch(BASE), batch_size=batch_size)
    store.graph_exists.assert_not_called()


def test_invalid_input_cli_fails_before_database_connection(tmp_path, monkeypatch):
    factory = MagicMock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr("ringlens.cli.GraphStore", factory)
    result = CliRunner().invoke(app, ["ingest", "--input", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "input directory" in result.output
    factory.assert_not_called()


def test_cli_graph_override_and_json_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    factory = MagicMock()
    monkeypatch.setattr("ringlens.cli.GraphStore", factory)
    result = ImportResult(
        batch_id=read_batch(BASE).manifest.batch_id,
        status="DRY_RUN",
        counts={"accounts": 50},
        as_of=1792058400,
    )
    importer = MagicMock(return_value=result)
    monkeypatch.setattr("ringlens.cli.ingest_batch", importer)
    output = CliRunner().invoke(
        app, ["ingest", "--input", str(BASE), "--dry-run", "--graph", "ringlens_test_cli"]
    )
    assert output.exit_code == 0, output.output
    assert json.loads(output.output)["status"] == "DRY_RUN"
    assert factory.call_args.args[0].graph_name == "ringlens_test_cli"
    assert importer.call_args.kwargs["dry_run"] is True


def test_empty_graph_verification_is_structured_and_read_only():
    store = MagicMock(spec=GraphStore)
    store.graph_exists.return_value = False
    report = verify_graph(store)
    assert not report.ok and "No imported Dataset" in report.issues[0]
    assert set(report.counts.values()) == {0}
    store.write.assert_not_called()


def test_cli_failed_verification_returns_report_and_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ringlens.cli.GraphStore", MagicMock())
    monkeypatch.setattr(
        "ringlens.cli.verify_graph",
        MagicMock(return_value=VerificationReport(issues=["unfinished import"])),
    )
    result = CliRunner().invoke(app, ["graph", "verify", "--graph", "ringlens_test_cli"])
    assert result.exit_code == 1
    assert json.loads(result.output)["issues"] == ["unfinished import"]


def test_cli_unreachable_graph_also_returns_structured_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "ringlens.cli.GraphStore", MagicMock(side_effect=GraphConnectionError("unreachable"))
    )
    result = CliRunner().invoke(app, ["graph", "verify"])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False
    assert "unreachable" in json.loads(result.output)["issues"][0]
