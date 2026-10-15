from unittest.mock import MagicMock

import pytest
from falkordb.exceptions import SchemaVersionMismatchException
from redis.exceptions import AuthenticationError, ConnectionError, ResponseError, TimeoutError

from ringlens.config import Settings
from ringlens.graph_store import (
    GraphAuthenticationError,
    GraphConnectionError,
    GraphQueryError,
    GraphStore,
    GraphTimeoutError,
    GraphUnavailableError,
)


@pytest.fixture
def connection(monkeypatch):
    client = MagicMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("ringlens.graph_store.FalkorDB", factory)
    with GraphStore(Settings(_env_file=None, falkordb_password="private-password")) as store:
        yield store, client, factory


def test_parameterized_read_write_and_deadlines(connection):
    store, client, factory = connection
    graph = client.select_graph.return_value
    params = {"value": "quoted ' evidence $"}
    assert store.read("RETURN $value", params) is graph.ro_query.return_value
    graph.ro_query.assert_called_once_with("RETURN $value", params=params, timeout=5000)
    store.write("CREATE (:Marker {value: $value})", params, timeout_ms=9000)
    graph.query.assert_called_once_with(
        "CREATE (:Marker {value: $value})", params=params, timeout=9000
    )
    assert factory.call_args.kwargs["socket_connect_timeout"] == 3
    assert factory.call_args.kwargs["socket_timeout"] == 12
    assert factory.call_args.kwargs["retry"].get_retries() == 0


def test_health_does_not_run_a_graph_query(connection):
    store, client, _ = connection
    store.check_health()
    client.connection.ping.assert_called_once()
    client.config_get.assert_called_once_with("TIMEOUT_DEFAULT")
    client.list_graphs.assert_called_once()
    client.select_graph.return_value.query.assert_not_called()
    client.select_graph.return_value.ro_query.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1, 10001, True, 1.5])
def test_invalid_timeout_never_reaches_database(connection, timeout):
    store, client, _ = connection
    with pytest.raises(ValueError, match="milliseconds"):
        store.write("CREATE ()", timeout_ms=timeout)
    client.select_graph.return_value.query.assert_not_called()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("private-password"), GraphAuthenticationError),
        (ConnectionError("private-password"), GraphConnectionError),
        (TimeoutError("private-password"), GraphTimeoutError),
        (ResponseError("Query timed out private-password"), GraphTimeoutError),
        (ResponseError("invalid evidence private-password"), GraphQueryError),
        (ValueError("invalid private-password parameter"), GraphQueryError),
        (SchemaVersionMismatchException(1), GraphQueryError),
    ],
)
def test_errors_are_explicit_and_redacted(connection, error, expected):
    store, client, _ = connection
    client.select_graph.return_value.query.side_effect = error
    with pytest.raises(expected) as raised:
        store.write("secret query", {"secret": "secret evidence"})
    assert "private-password" not in str(raised.value)
    assert "secret evidence" not in str(raised.value)
    assert "secret query" not in str(raised.value)
    if expected in (GraphConnectionError, GraphTimeoutError):
        assert "outcome is unknown" in str(raised.value)
    assert client.select_graph.return_value.query.call_count == 1


def test_plain_redis_is_not_healthy(connection):
    store, client, _ = connection
    client.config_get.side_effect = ResponseError("unknown command GRAPH.CONFIG")
    with pytest.raises(GraphUnavailableError, match="not plain Redis"):
        store.check_health()


def test_close_disconnects_and_prevents_reuse(connection):
    store, client, _ = connection
    store.close()
    store.close()
    client.connection.close.assert_called_once()
    client.connection.connection_pool.disconnect.assert_called_once()
    with pytest.raises(GraphConnectionError, match="closed"):
        store.read("RETURN 1")
