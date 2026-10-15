"""Real FalkorDB checks. Never flush databases or touch application graphs."""

import os
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

from ringlens.config import Settings
from ringlens.graph_store import GraphStore, GraphStoreError

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
WRITE_MARKER = "CREATE (:Phase1Marker {token: $token})"
READ_MARKER = "MATCH (m:Phase1Marker {token: $token}) RETURN m.token"


@pytest.fixture
def test_graph():
    name = f"ringlens_test_{uuid4().hex}"
    settings = Settings(graph_name=name)
    with GraphStore(settings) as store:
        store.check_health()
        # Use the official client's lifecycle methods only for this unique test graph.
        client = store._db
        assert name not in client.list_graphs(), "Refusing to touch an existing graph"
        try:
            yield store, settings, uuid4().hex + " ' \" \\ $"
        finally:
            if name in client.list_graphs():
                client.select_graph(name).delete()
            assert name not in client.list_graphs()


def test_marker_round_trip_and_read_only_enforcement(test_graph):
    store, _, token = test_graph
    store.write(WRITE_MARKER, {"token": token})
    assert store.read(READ_MARKER, {"token": token}).result_set == [[token]]
    with pytest.raises(GraphStoreError):
        store.read(WRITE_MARKER, {"token": "must-not-be-written"})
    assert store.read("MATCH (m:Phase1Marker) RETURN count(m)").result_set == [[1]]


@pytest.mark.persistence
def test_marker_survives_compose_restart(test_graph):
    store, settings, token = test_graph
    assert settings.falkordb_host in {"127.0.0.1", "localhost"}, "Restart test requires loopback"
    assert shutil.which("docker"), "Docker CLI is required for --run-persistence"
    compose_env = os.environ.copy()
    compose_env["RINGLENS_FALKORDB_PORT"] = str(settings.falkordb_port)
    if settings.falkordb_password:
        compose_env["RINGLENS_FALKORDB_PASSWORD"] = settings.falkordb_password.get_secret_value()

    def compose(*args):
        # Do not echo subprocess output: Docker can expand credentials in errors.
        result = subprocess.run(
            ["docker", "compose", "--file", str(ROOT / "compose.yaml"), *args],
            cwd=ROOT,
            env=compose_env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, (
            f"docker compose {args[0]} failed; check Docker/service state"
        )
        return result.stdout.strip()

    published = compose("port", "falkordb", "6379")
    assert published == f"127.0.0.1:{settings.falkordb_port}", (
        "Refusing to restart another endpoint"
    )
    server_id = store._db.connection.info("server")["run_id"]
    config = store._db.connection.config_get("appendonly", "appendfsync", "dir")
    assert config["appendonly"] == "yes"
    assert config["appendfsync"] == "everysec"
    assert config["dir"] == "/var/lib/falkordb/data"
    store.write(WRITE_MARKER, {"token": token})
    assert store.read(READ_MARKER, {"token": token}).result_set == [[token]]

    compose("restart", "--timeout", "30", "falkordb")
    store._db.connection.connection_pool.disconnect()
    deadline = time.monotonic() + 45
    while True:
        try:
            store.check_health()
            break
        except GraphStoreError:
            if time.monotonic() >= deadline:
                pytest.fail("FalkorDB did not recover within 45 seconds after restart")
            time.sleep(0.5)

    assert store._db.connection.info("server")["run_id"] != server_id
    assert store.read(READ_MARKER, {"token": token}).result_set == [[token]]
