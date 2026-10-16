from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from ringlens.cli import app
from ringlens.config import Settings
from ringlens.graph_store import (
    GraphAuthenticationError,
    GraphConnectionError,
    GraphUnavailableError,
)
from ringlens.schema import SchemaResult

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    import os

    for key in os.environ:
        if key.startswith("RINGLENS_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def test_help_does_not_connect(monkeypatch):
    factory = MagicMock()
    monkeypatch.setattr("ringlens.cli.GraphStore", factory)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output
    factory.assert_not_called()


def test_bad_configuration_is_readable_without_echoing_input(monkeypatch):
    monkeypatch.setenv("RINGLENS_FALKORDB_PORT", "private-password")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 2
    assert "RINGLENS_FALKORDB_PORT" in result.output
    assert "valid integer" in result.output
    assert "private-password" not in result.output


@pytest.mark.parametrize(
    "error",
    [
        GraphConnectionError("Cannot connect; run docker compose up -d --wait."),
        GraphAuthenticationError("Authentication failed; check RINGLENS_FALKORDB_PASSWORD."),
        GraphUnavailableError("FalkorDB unavailable; check the image."),
    ],
)
def test_dependency_failure_exits_nonzero(monkeypatch, error):
    monkeypatch.setattr("ringlens.cli.GraphStore", MagicMock(side_effect=error))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert str(error) in result.output


def test_doctor_success(monkeypatch):
    monkeypatch.setattr("ringlens.cli.Settings", lambda: Settings(_env_file=None))
    factory = MagicMock()
    monkeypatch.setattr("ringlens.cli.GraphStore", factory)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Doctor OK" in result.output
    factory.return_value.__enter__.return_value.check_health.assert_called_once()


def test_schema_help_and_init(monkeypatch):
    assert runner.invoke(app, ["schema", "--help"]).exit_code == 0
    assert runner.invoke(app, ["schema", "init", "--help"]).exit_code == 0
    monkeypatch.setattr("ringlens.cli.GraphStore", MagicMock())
    initializer = MagicMock(return_value=SchemaResult(20, 0))
    monkeypatch.setattr("ringlens.cli.initialize_schema", initializer)
    result = runner.invoke(app, ["schema", "init"])
    assert result.exit_code == 0
    assert "No records seeded" in result.output
    assert "do not enforce uniqueness" in result.output
    initializer.assert_called_once()


def test_schema_init_uses_readable_configuration_and_database_errors(monkeypatch):
    monkeypatch.setenv("RINGLENS_FALKORDB_PORT", "invalid")
    assert runner.invoke(app, ["schema", "init"]).exit_code == 2
    monkeypatch.delenv("RINGLENS_FALKORDB_PORT")
    monkeypatch.setattr(
        "ringlens.cli.GraphStore", MagicMock(side_effect=GraphConnectionError("offline"))
    )
    result = runner.invoke(app, ["schema", "init"])
    assert result.exit_code == 1
    assert "Schema initialization failed" in result.output
