from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ringlens.graph_store import GraphQueryError, GraphStore
from ringlens.schema import INDEXES, SchemaInitializationError, initialize_schema


def catalog(*, status="OPERATIONAL", omit=None):
    # FalkorDB v4.20.7 groups indexed fields/types into one catalog row per label.
    labels = {}
    for label, prop in INDEXES:
        if (label, prop) != omit:
            labels.setdefault(label, {})[prop] = ["RANGE"]
    return SimpleNamespace(
        result_set=[[label, types, "NODE", status] for label, types in labels.items()]
    )


def test_schema_init_twice_only_creates_indexes():
    store = MagicMock(spec=GraphStore)
    store.graph_exists.return_value = False
    store.read.return_value = catalog()
    result = initialize_schema(store)
    assert result.created_indexes == len(INDEXES)
    assert all(call.args[0].startswith("CREATE INDEX FOR") for call in store.write.call_args_list)
    store.write.reset_mock()
    store.graph_exists.return_value = True
    second = initialize_schema(store)
    assert second.created_indexes == 0
    assert second.existing_indexes == len(INDEXES)
    store.write.assert_not_called()


def test_fulltext_does_not_satisfy_range_index_and_existing_data_is_preserved():
    store = MagicMock(spec=GraphStore)
    store.graph_exists.return_value = True
    initial = catalog(omit=("Account", "id"))
    initial.result_set.append(["Account", {"id": ["FULLTEXT"]}, "NODE", "OPERATIONAL"])
    initial.result_set.append(["Unrelated", {"other": ["RANGE"]}, "NODE", "OPERATIONAL"])
    store.read.side_effect = [initial, catalog()]
    result = initialize_schema(store)
    assert result.created_indexes == 1
    store.write.assert_called_once_with("CREATE INDEX FOR (n:Account) ON (n.id)")


def test_racing_initializer_is_only_accepted_when_index_exists():
    store = MagicMock(spec=GraphStore)
    store.graph_exists.return_value = True
    store.read.side_effect = [catalog(omit=("Account", "id")), catalog(), catalog()]
    store.write.side_effect = GraphQueryError("already indexed")
    assert initialize_schema(store).created_indexes == 0
    store.read.side_effect = None
    store.read.return_value = catalog(omit=("Account", "id"))
    with pytest.raises(GraphQueryError):
        initialize_schema(store)


def test_waits_for_index_population(monkeypatch):
    store = MagicMock(spec=GraphStore)
    store.graph_exists.return_value = True
    store.read.side_effect = [catalog(), catalog(status="UNDER CONSTRUCTION"), catalog()]
    monkeypatch.setattr("ringlens.schema.time.sleep", lambda _: None)
    initialize_schema(store)
    assert store.read.call_count == 3


def test_unfinished_indexes_fail_without_dropping_anything(monkeypatch):
    store = MagicMock(spec=GraphStore)
    store.graph_exists.return_value = True
    store.read.return_value = catalog(status="UNDER CONSTRUCTION")
    monkeypatch.setattr("ringlens.schema.time.monotonic", MagicMock(side_effect=[0, 31]))
    with pytest.raises(SchemaInitializationError, match="rerun schema init"):
        initialize_schema(store)
    store.write.assert_not_called()
