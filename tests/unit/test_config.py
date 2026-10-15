import pytest
from pydantic import ValidationError

from ringlens.config import Settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    import os

    for key in os.environ:
        if key.startswith("RINGLENS_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def test_defaults_and_secret_redaction(monkeypatch):
    monkeypatch.setenv("RINGLENS_FALKORDB_PASSWORD", "private-password")
    settings = Settings()
    assert settings.graph_name == "ringlens_demo"
    assert settings.falkordb_port == 6379
    assert "private-password" not in repr(settings)
    assert "private-password" not in settings.model_dump_json()


def test_dotenv_and_environment_precedence(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "RINGLENS_GRAPH_NAME=dotenv_graph\nRINGLENS_LOG_LEVEL=debug\nUNRELATED_SETTING=ok\n"
    )
    monkeypatch.setenv("RINGLENS_GRAPH_NAME", "environment_graph")
    settings = Settings()
    assert settings.graph_name == "environment_graph"
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("falkordb_port", 0),
        ("falkordb_port", 65536),
        ("falkordb_port", "not-a-port"),
        ("falkordb_host", ""),
        ("falkordb_host", "redis://secret@localhost"),
        ("falkordb_host", "local host"),
        ("graph_name", ""),
        ("graph_name", "graph with spaces"),
        ("graph_name", "x" * 129),
        ("log_level", "verbose"),
    ],
)
def test_invalid_settings(key, value):
    with pytest.raises(ValidationError):
        Settings(**{key: value})


def test_empty_password_supports_existing_unauthenticated_server():
    assert Settings(falkordb_password="").falkordb_password is None
