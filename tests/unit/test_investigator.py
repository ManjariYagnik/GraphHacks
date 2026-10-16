"""Offline failure semantics and CLI input validation require no database."""

import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from ringlens.cli import app
from ringlens.graph_store import GraphConnectionError
from ringlens.investigation_models import InvestigationConfig
from ringlens.investigator import (
    InvestigationDataUnavailable,
    InvestigationInputError,
    investigate,
    render_text,
)


def test_graph_probe_failure_is_incomplete_and_retains_requested_seed():
    store = MagicMock()
    store.graph_exists.side_effect = GraphConnectionError("Cannot connect to FalkorDB.")
    result = investigate(store, "ref_seed")
    assert result.seed_refund_id == "ref_seed" and result.completeness == "INCOMPLETE"
    assert result.total_score is result.priority is result.analysis is None
    assert result.errors[0].code == "QUERY_FAILED"
    assert result.errors[0].query_id == "graph.exists.v1"
    assert "No final score" in render_text(result)
    store.write.assert_not_called()


@pytest.mark.parametrize(
    ("refund_id", "as_of"),
    [("bad", None), ("ref_", None), ("ref_x", True), ("ref_x", "100"), ("ref_x", -1)],
)
def test_invalid_input_precedes_database_access(refund_id, as_of):
    store = MagicMock()
    with pytest.raises(InvestigationInputError):
        investigate(store, refund_id, as_of=as_of)
    store.graph_exists.assert_not_called()
    store.read.assert_not_called()
    store.write.assert_not_called()


def test_absent_graph_is_data_unavailable_not_a_low_result():
    store = MagicMock()
    store.graph_exists.return_value = False
    with pytest.raises(InvestigationDataUnavailable, match="ingest"):
        investigate(store, "ref_x")
    store.read.assert_not_called()
    store.write.assert_not_called()


def test_cli_help_and_invalid_utc_do_not_open_database(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr("ringlens.cli.GraphStore", factory)
    runner = CliRunner()
    assert runner.invoke(app, ["investigate", "--help"]).exit_code == 0
    for time in ("2026-10-15T10:00:00", "2026-10-15T10:00:00+05:30", "now"):
        result = runner.invoke(
            app, ["investigate", "--refund-id", "ref_x", "--as-of", time, "--format", "json"]
        )
        assert result.exit_code == 2
        assert json.loads(result.output)["error"]["kind"] == "INVALID_INPUT"
    factory.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--max-accounts", "0"],
        ["--max-evidence", "0"],
        ["--window-days", "0"],
        ["--format", "yaml"],
    ],
)
def test_cli_rejects_invalid_bounds_and_formats_without_database(monkeypatch, arguments):
    factory = MagicMock()
    monkeypatch.setattr("ringlens.cli.GraphStore", factory)
    result = CliRunner().invoke(app, ["investigate", "--refund-id", "ref_x", *arguments])
    assert result.exit_code == 2
    factory.assert_not_called()


def test_incomplete_cli_is_structured_and_nonzero(monkeypatch):
    store = MagicMock()
    store.graph_exists.side_effect = GraphConnectionError("Cannot connect to FalkorDB.")
    factory = MagicMock()
    factory.return_value.__enter__.return_value = store
    monkeypatch.setattr("ringlens.cli.GraphStore", factory)
    result = CliRunner().invoke(app, ["investigate", "--refund-id", "ref_x", "--format", "json"])
    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["completeness"] == "INCOMPLETE" and payload["priority"] is None
    assert payload["config"] == InvestigationConfig().model_dump()
