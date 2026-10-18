"""Bounded display projection of actual FalkorDB evidence paths."""

from ringlens.verify import stable_path

COLORS = {
    "Account": "#0F766E",
    "Order": "#2563EB",
    "Device": "#6D28D9",
    "Address": "#4F46E5",
    "Refund": "#BE185D",
}
SYMBOLS = {
    "Account": "circle",
    "Order": "roundRect",
    "Device": "diamond",
    "Address": "triangle",
    "Refund": "pin",
}
SHORT_PREFIXES = {
    "Account": "AC",
    "Order": "OR",
    "Device": "DV",
    "Address": "AD",
    "Refund": "RF",
}


def graph_projection(
    result, *, selected_path_id: str | None = None, max_nodes: int = 80, max_edges: int = 120
) -> dict:
    if max_nodes < 1 or max_edges < 1:
        raise ValueError("Display limits must be positive.")
    paths = list(result.paths)
    if selected_path_id:
        paths.sort(key=lambda path: (path.id != selected_path_id, path.id))
    else:
        paths.sort(key=lambda path: path.id)
    all_nodes, all_edges, shown_nodes, shown_edges = {}, {}, {}, {}
    for proof in paths:
        path = stable_path(proof.path)
        for node in path["nodes"]:
            all_nodes[node["id"]] = node
        for edge in path["edges"]:
            key = (edge["source_id"], edge["type"], edge["target_id"])
            all_edges[key] = edge
        for node in path["nodes"]:
            if node["id"] not in shown_nodes and len(shown_nodes) < max_nodes:
                shown_nodes[node["id"]] = node
        for edge in path["edges"]:
            key = (edge["source_id"], edge["type"], edge["target_id"])
            if (
                key not in shown_edges
                and len(shown_edges) < max_edges
                and edge["source_id"] in shown_nodes
                and edge["target_id"] in shown_nodes
            ):
                shown_edges[key] = edge
    selected_edges = set()
    selected_nodes = set()
    if selected_path_id:
        for proof in paths:
            if proof.id == selected_path_id:
                selected_nodes = {node["id"] for node in stable_path(proof.path)["nodes"]}
                selected_edges = {
                    (edge["source_id"], edge["type"], edge["target_id"])
                    for edge in stable_path(proof.path)["edges"]
                }
                break
    # Assign display labels from every path, so highlighting or capping the view
    # cannot change a node's short label within this assessment.
    short_ids = {}
    prefix_counts = {}
    for id_, node in sorted(all_nodes.items(), key=lambda item: (item[1]["labels"][0], item[0])):
        prefix = SHORT_PREFIXES.get(node["labels"][0], "N")
        prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
        short_ids[id_] = f"{prefix}{prefix_counts[prefix]}"
    nodes = []
    for id_, node in sorted(shown_nodes.items()):
        label = node["labels"][0]
        nodes.append(
            {
                "id": id_,
                "name": short_ids[id_],
                "category": label,
                "symbol": SYMBOLS.get(label, "circle"),
                "symbolSize": 36 if id_ in selected_nodes else 30 if label == "Account" else 25,
                "itemStyle": {
                    "color": COLORS.get(label, "#475569"),
                    "borderColor": "#172033" if id_ in selected_nodes else "#FFFFFF",
                    "borderWidth": 3 if id_ in selected_nodes else 1.5,
                    "shadowBlur": 12 if id_ in selected_nodes else 0,
                    "shadowColor": "rgba(109,40,217,.28)",
                },
                "value": id_,
            }
        )
    edges = []
    for key in sorted(shown_edges):
        edges.append(
            {
                "source": key[0],
                "target": key[2],
                "name": key[1],
                "value": key[1],
                "lineStyle": {
                    "width": 4 if key in selected_edges else 1.5,
                    "color": "#6D28D9" if key in selected_edges else "#CBD5E1",
                    "opacity": 1 if key in selected_edges else 0.8,
                },
            }
        )
    return {
        "nodes": nodes,
        "edges": edges,
        "node_details": shown_nodes,
        "short_ids": short_ids,
        "omitted_nodes": len(all_nodes) - len(shown_nodes),
        "omitted_edges": len(all_edges) - len(shown_edges),
    }


def echarts_spec(projection: dict) -> dict:
    return {
        "backgroundColor": "#FFFFFF",
        "textStyle": {"color": "#334155", "fontFamily": "sans-serif"},
        "tooltip": {"trigger": "item"},
        "legend": {
            "data": list(COLORS),
            "bottom": 0,
            "textStyle": {"color": "#475569", "fontSize": 12},
        },
        "animationDuration": 300,
        "series": [
            {
                "type": "graph",
                "layout": "force",
                "roam": True,
                "draggable": True,
                "data": projection["nodes"],
                "links": projection["edges"],
                "categories": [{"name": label} for label in COLORS],
                "label": {"show": True, "position": "right", "fontSize": 11, "color": "#334155"},
                "edgeLabel": {
                    "show": True,
                    "formatter": "{c}",
                    "fontSize": 9,
                    "color": "#475569",
                },
                "edgeSymbol": ["none", "arrow"],
                "edgeSymbolSize": 8,
                "force": {"repulsion": 230, "edgeLength": 120},
                "emphasis": {"focus": "adjacency"},
            }
        ],
    }
