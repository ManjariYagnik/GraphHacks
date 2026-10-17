"""Read and verify FalkorDB source truth and return genuine graph explanation paths."""

from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256

from falkordb.path import Path as GraphPath
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from ringlens import import_queries as q
from ringlens.dataset_io import SOURCE_MODELS, DatasetBatch
from ringlens.graph_store import GraphStore
from ringlens.models import (
    AccountId,
    Dataset,
    ImportBatch,
    Manifest,
    RefundId,
    canonical_json,
)
from ringlens.schema import source_node_properties
from ringlens.validation import (
    SourceRecords,
    canonical_record_bytes,
    manifest_content_hash,
    source_hash,
    validate_manifest,
    validate_records,
)


class GraphIntegrityError(ValueError):
    """Actionable graph invariant error, without raw server response data."""


@dataclass
class GraphState:
    nodes: dict[str, list[tuple[int, dict]]] = field(
        default_factory=lambda: {k: [] for k in q.SOURCE_LABELS}
    )
    datasets: list[tuple[int, dict]] = field(default_factory=list)
    batches: list[tuple[int, dict]] = field(default_factory=list)
    edges: list[list] = field(default_factory=list)


@dataclass(frozen=True)
class BatchHistory:
    batch: ImportBatch
    manifest: Manifest
    source_ids: tuple[str, ...]


class VerificationReport(BaseModel):
    ok: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    node_counts: dict[str, int] = Field(default_factory=dict)
    expected_counts: dict[str, int] = Field(default_factory=dict)
    relationship_counts: dict[str, int] = Field(default_factory=dict)
    as_of: int | None = None
    dataset_status: str | None = None
    batches: list[dict[str, str]] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


def read_state(store: GraphStore) -> GraphState:
    state = GraphState()
    if not store.graph_exists():
        return state
    state.nodes = {kind: store.read(query).result_set for kind, query in q.READ_NODES.items()}
    state.datasets = store.read(q.READ_DATASETS).result_set
    state.batches = store.read(q.READ_BATCHES).result_set
    state.edges = store.read(q.READ_EDGES, {"types": [*q.SOURCE_RELATIONS, "HAS_BATCH"]}).result_set
    return state


def read_history(state: GraphState) -> tuple[Dataset | None, list[BatchHistory]]:
    if not state.datasets:
        if state.batches or any(edge[1] == "HAS_BATCH" for edge in state.edges):
            raise GraphIntegrityError(
                "Import batches exist without a Dataset; explicit recovery is required."
            )
        return None, []
    if len(state.datasets) != 1:
        raise GraphIntegrityError("Expected exactly one Dataset node in the graph.")
    dataset_node, props = state.datasets[0]
    try:
        dataset = Dataset.model_validate(props)
        history = []
        seen = set()
        links = [edge for edge in state.edges if edge[1] == "HAS_BATCH"]
        if len(links) != len(state.batches):
            raise GraphIntegrityError(
                "Every ImportBatch must have exactly one HAS_BATCH relationship."
            )
        for node_id, properties in state.batches:
            parents = [edge for edge in links if edge[2] == node_id]
            if len(parents) != 1 or parents[0][0] != dataset_node:
                raise GraphIntegrityError(
                    "An ImportBatch has a missing, duplicate, or wrong Dataset link."
                )
            if "dataset_id" in properties:
                raise GraphIntegrityError(
                    "ImportBatch.dataset_id must be represented by its Dataset edge."
                )
            batch = ImportBatch.model_validate(
                {"completed_at": None, "error": None, **properties, "dataset_id": dataset.id}
            )
            if batch.id in seen:
                raise GraphIntegrityError("Duplicate ImportBatch ID in graph.")
            seen.add(batch.id)
            provenance = parents[0][3]
            if set(provenance) != {"manifest_json", "source_ids"}:
                raise GraphIntegrityError(
                    "HAS_BATCH is missing its importer manifest/membership provenance."
                )
            manifest = Manifest.model_validate_json(provenance["manifest_json"])
            validate_manifest(manifest)
            if canonical_json(manifest.model_dump(mode="json")) != provenance["manifest_json"]:
                raise GraphIntegrityError("Stored manifest is not canonical JSON.")
            ids = provenance["source_ids"]
            if (
                not isinstance(ids, list)
                or any(type(id_) is not str for id_ in ids)
                or len(ids) != len(set(ids))
            ):
                raise GraphIntegrityError(
                    "Stored batch membership must contain distinct source IDs."
                )
            if (
                manifest.batch_id != batch.id
                or manifest.dataset_id != dataset.id
                or manifest_content_hash(manifest) != batch.content_hash
                or manifest.mode != batch.mode
            ):
                raise GraphIntegrityError(
                    "ImportBatch identity/content does not match its stored manifest."
                )
            history.append(BatchHistory(batch, manifest, tuple(ids)))
        if len([item for item in history if item.manifest.mode == "base"]) != 1:
            raise GraphIntegrityError("Expected exactly one base batch in the import history.")
        return dataset, history
    except (ValidationError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, GraphIntegrityError):
            raise
        raise GraphIntegrityError(
            "Invalid Dataset, ImportBatch, or stored manifest metadata."
        ) from None


def expected_edges(records: SourceRecords) -> set[tuple[str, str, str]]:
    result = set()
    for order in records.orders:
        result.update(
            (
                (order.account_id, "PLACED", order.id),
                (order.id, "USED_DEVICE", order.device_id),
                (order.id, "SHIPPED_TO", order.address_id),
            )
        )
    for refund in records.refunds:
        result.update(
            (
                (refund.account_id, "REQUESTED_REFUND", refund.id),
                (refund.id, "FOR_ORDER", refund.order_id),
            )
        )
    return result


def graph_records(
    state: GraphState, *, recovery_records: SourceRecords | None = None
) -> SourceRecords:
    """Reconstruct canonical references from actual edges, never from cached source JSON.

    On retry only, incoming records can supply missing edges for their own partially
    written nodes. Existing properties/hashes and every present edge must still agree.
    """
    internal = {}
    existing = {}
    recovery = (
        {row.id: row for kind in q.SOURCE_LABELS for row in getattr(recovery_records, kind)}
        if recovery_records
        else {}
    )
    for kind, nodes in state.nodes.items():
        for node_id, props in nodes:
            id_ = props.get("id")
            if type(id_) is not str or id_ in existing or node_id in internal:
                raise GraphIntegrityError(
                    "Source nodes require unique, nonempty type-prefixed IDs and labels."
                )
            internal[node_id] = (kind, props)
            existing[id_] = (kind, props)

    def reference(node_id, relation, target_kind, outgoing):
        edges = [
            edge
            for edge in state.edges
            if edge[1] == relation and edge[0 if outgoing else 2] == node_id
        ]
        if len(edges) != 1:
            raise GraphIntegrityError(
                f"A source node must have exactly one {relation} relationship."
            )
        target = internal.get(edges[0][2 if outgoing else 0])
        if target is None or target[0] != target_kind:
            raise GraphIntegrityError(
                f"A {relation} relationship has an invalid or orphan endpoint."
            )
        return target[1]["id"]

    records = {kind: [] for kind in q.SOURCE_LABELS}
    for kind, nodes in state.nodes.items():
        for node_id, props in nodes:
            id_ = props["id"]
            if id_ in recovery:
                row = recovery[id_]
                if type(row) is not SOURCE_MODELS[kind] or props != source_node_properties(row):
                    raise GraphIntegrityError(
                        "Existing source ID has conflicting immutable properties/hash."
                    )
            else:
                values = {key: value for key, value in props.items() if key != "source_hash"}
                if kind == "orders":
                    values.update(
                        account_id=reference(node_id, "PLACED", "accounts", False),
                        device_id=reference(node_id, "USED_DEVICE", "devices", True),
                        address_id=reference(node_id, "SHIPPED_TO", "addresses", True),
                    )
                elif kind == "refunds":
                    values.update(
                        account_id=reference(node_id, "REQUESTED_REFUND", "accounts", False),
                        order_id=reference(node_id, "FOR_ORDER", "orders", True),
                    )
                try:
                    row = SOURCE_MODELS[kind].model_validate(values)
                except ValidationError:
                    raise GraphIntegrityError(
                        f"{kind}: stored properties violate the source model."
                    ) from None
                if props.get("source_hash") != source_hash(row):
                    raise GraphIntegrityError(
                        "Stored source_hash is missing or disagrees with actual graph data."
                    )
            records[kind].append(row)
    if recovery_records:
        for kind in q.SOURCE_LABELS:
            records[kind].extend(
                row for row in getattr(recovery_records, kind) if row.id not in existing
            )
    result = SourceRecords.model_validate(records)
    actual = []
    for start, relation, end, _ in state.edges:
        if relation not in q.SOURCE_RELATIONS:
            continue
        if start not in internal or end not in internal:
            raise GraphIntegrityError(
                "A canonical relationship has an orphan or wrong-label endpoint."
            )
        actual.append((internal[start][1]["id"], relation, internal[end][1]["id"]))
    if len(actual) != len(set(actual)):
        raise GraphIntegrityError("Duplicate canonical relationships exist in the graph.")
    expected = expected_edges(result)
    if set(actual) - expected or (not recovery_records and expected - set(actual)):
        raise GraphIntegrityError(
            "Canonical relationships do not match source ownership/references."
        )
    return result


def verify_history_content(
    history: list[BatchHistory], records: SourceRecords, *, extra: DatasetBatch | None = None
) -> dict[str, int]:
    indexed = {row.id: (kind, row) for kind in q.SOURCE_LABELS for row in getattr(records, kind)}
    expected_ids = set()
    for item in history:
        members = item.source_ids
        if any(id_ not in indexed for id_ in members):
            raise GraphIntegrityError(
                "An imported batch is missing source nodes declared by its provenance."
            )
        expected_ids.update(members)
        for file in item.manifest.files:
            rows = sorted(
                (indexed[id_][1] for id_ in members if indexed[id_][0] == file.kind),
                key=lambda row: row.id,
            )
            data = b"".join(canonical_record_bytes(row) for row in rows)
            if len(rows) != file.count or sha256(data).hexdigest() != file.sha256:
                raise GraphIntegrityError(
                    "Stored source data/counts do not match an applied batch manifest."
                )
    if extra:
        expected_ids.update(
            row.id for kind in q.SOURCE_LABELS for row in getattr(extra.records, kind)
        )
    if set(indexed) != expected_ids:
        raise GraphIntegrityError("Graph contains source IDs not accounted for by applied batches.")
    return {kind: sum(indexed[id_][0] == kind for id_ in expected_ids) for kind in q.SOURCE_LABELS}


def verify_graph(
    store: GraphStore, *, expected: SourceRecords | None = None, pending_batch: str | None = None
) -> VerificationReport:
    state = read_state(store)
    report = VerificationReport(
        counts={
            kind: len({props.get("id") for _, props in rows}) for kind, rows in state.nodes.items()
        },
        node_counts={kind: len(rows) for kind, rows in state.nodes.items()},
        relationship_counts=dict(Counter(edge[1] for edge in state.edges)),
    )
    try:
        dataset, history = read_history(state)
        if dataset is None:
            raise GraphIntegrityError(
                "No imported Dataset exists; import a validated base batch first."
            )
        report.as_of, report.dataset_status = dataset.as_of, dataset.status
        report.batches = [{"id": item.batch.id, "status": item.batch.status} for item in history]
        if pending_batch and not any(item.batch.id == pending_batch for item in history):
            raise GraphIntegrityError("Pending batch is missing from import history.")
        for item in history:
            required = "LOADING" if item.batch.id == pending_batch else "READY"
            if item.batch.status != required:
                raise GraphIntegrityError(
                    "An import batch is unfinished; retry it before using assessments."
                )
        if dataset.status != ("INGESTING" if pending_batch else "READY"):
            raise GraphIntegrityError("Dataset is not READY; its assessment must not be used.")
        committed = [item.manifest.as_of for item in history if item.batch.status == "READY"]
        expected_as_of = max(committed) if committed else history[0].manifest.as_of
        if dataset.as_of != expected_as_of:
            raise GraphIntegrityError("Dataset.as_of does not match successful applied batches.")
        horizon = max(item.manifest.as_of for item in history)
        records = graph_records(state)
        validate_records(records, as_of=horizon)
        report.expected_counts = verify_history_content(history, records)
        if expected is not None:
            for kind in q.SOURCE_LABELS:
                actual_rows = {row.id: row for row in getattr(records, kind)}
                if actual_rows != {row.id: row for row in getattr(expected, kind)}:
                    raise GraphIntegrityError(
                        "Actual graph source records differ from the validated import plan."
                    )
        report.ok = True
    except ValueError as error:
        report.issues.append(str(error))
    return report


def graph_stats(store: GraphStore) -> dict:
    state = read_state(store)
    labels = {
        q.SOURCE_LABELS[k]: {
            "nodes": len(rows),
            "distinct_ids": len({p.get("id") for _, p in rows}),
        }
        for k, rows in state.nodes.items()
    }
    relationships = {}
    if store.graph_exists():
        for label in (
            "Dataset",
            "ImportBatch",
            "Investigation",
            "Evidence",
            "Review",
            "Assessment",
            "AgentRun",
        ):
            count, distinct = store.read(
                f"MATCH (n:{label}) RETURN count(n), count(DISTINCT n.id)"
            ).result_set[0]
            labels[label] = {"nodes": count, "distinct_ids": distinct}
        relationships = dict(store.read("MATCH ()-[r]->() RETURN type(r), count(r)").result_set)
    return {
        "labels": labels,
        "relationships": relationships,
        "datasets": [props for _, props in state.datasets],
    }


def sample_paths(
    store: GraphStore,
    *,
    refund_id: str | None = None,
    account_id: str | None = None,
    limit: int = 5,
) -> dict:
    report = verify_graph(store)
    if not report.ok:
        raise GraphIntegrityError(
            "Graph verification failed; paths require a complete READY dataset."
        )
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    if refund_id is None:
        rows = store.read("MATCH (r:Refund) RETURN r.id ORDER BY r.id LIMIT 1").result_set
        refund_id = rows[0][0] if rows else None
    refund_paths = []
    params = {
        "as_of": report.as_of,
        "window_start": max(0, report.as_of - 30 * 86400),
        "limit": limit,
    }
    if refund_id is not None:
        refund_id = TypeAdapter(RefundId).validate_python(refund_id)
        refund_paths = store.read(q.REFUND_PATHS, {**params, "refund_id": refund_id}).result_set
        if account_id is None and refund_paths:
            account_id = refund_paths[0][1].nodes()[0].properties["id"]
    peers = []
    peer_count = 0
    if account_id is not None:
        account_id = TypeAdapter(AccountId).validate_python(account_id)
        peers = store.read(q.PEER_PATHS, {**params, "account_id": account_id}).result_set
        peer_count = store.read(q.PEER_COUNT, {**params, "account_id": account_id}).result_set[0][0]
    return {
        "as_of": report.as_of,
        "refund_id": refund_id,
        "account_id": account_id,
        "refund_paths": refund_paths,
        "peer_paths": peers,
        "unique_peer_count": peer_count,
    }


def path_json(path: GraphPath) -> dict:
    """Serialize graph-returned paths without discarding endpoint IDs or direction."""
    entity_ids = {node.id: node.properties.get("id") for node in path.nodes()}
    return {
        "nodes": [
            {
                "graph_id": node.id,
                "labels": node.labels,
                "id": node.properties.get("id"),
                "properties": dict(node.properties),
            }
            for node in path.nodes()
        ],
        "edges": [
            {
                "graph_id": edge.id,
                "type": edge.relation,
                "source_graph_id": edge.src_node,
                "target_graph_id": edge.dest_node,
                "source_id": entity_ids[edge.src_node],
                "target_id": entity_ids[edge.dest_node],
                "properties": dict(edge.properties),
            }
            for edge in path.edges()
        ],
    }


def stable_path(path: dict) -> dict:
    """Retain source IDs, properties, types and direction; omit physical driver IDs."""
    return {
        "nodes": [{k: v for k, v in node.items() if k != "graph_id"} for node in path["nodes"]],
        "edges": [
            {
                k: v
                for k, v in edge.items()
                if k not in {"graph_id", "source_graph_id", "target_graph_id"}
            }
            for edge in path["edges"]
        ],
    }
