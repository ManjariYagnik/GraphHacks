"""Human-readable graph labels must preserve real evidence identities."""

from types import SimpleNamespace

from ringlens.graph_view import graph_projection


def _path(path_id, account_id, order_id, device_id):
    return SimpleNamespace(
        id=path_id,
        path={
            "nodes": [
                {"id": account_id, "labels": ["Account"]},
                {"id": order_id, "labels": ["Order"]},
                {"id": device_id, "labels": ["Device"]},
            ],
            "edges": [
                {"source_id": account_id, "type": "PLACED", "target_id": order_id},
                {"source_id": order_id, "type": "USED_DEVICE", "target_id": device_id},
            ],
        },
    )


def test_short_labels_map_back_to_actual_ids_and_survive_path_selection():
    result = SimpleNamespace(
        paths=[
            _path("path_b", "acct_b", "ord_b", "dev_shared"),
            _path("path_a", "acct_a", "ord_a", "dev_shared"),
        ]
    )
    default = graph_projection(result, max_nodes=3)
    highlighted = graph_projection(result, selected_path_id="path_b", max_nodes=3)

    assert default["short_ids"] == highlighted["short_ids"]
    assert default["short_ids"] == {
        "acct_a": "AC1",
        "acct_b": "AC2",
        "dev_shared": "DV1",
        "ord_a": "OR1",
        "ord_b": "OR2",
    }
    assert len({node["name"] for node in default["nodes"]}) == len(default["nodes"])
    assert all(node["name"] == default["short_ids"][node["id"]] for node in default["nodes"])
    assert {node["value"] for node in default["nodes"]} == set(default["node_details"])
    assert all(edge["source"] in default["short_ids"] for edge in default["edges"])
    assert all(edge["target"] in default["short_ids"] for edge in default["edges"])
    assert default["omitted_nodes"] == 2
