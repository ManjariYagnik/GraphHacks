"""Server-controlled dataset registry, import boundary, and graph-derived dashboard data."""

import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from ringlens import cases
from ringlens.config import Settings
from ringlens.csv_adapter import CSVValidation, validate_csv_sources
from ringlens.graph_store import GraphStore
from ringlens.ingest import ImportResult, ingest_batch
from ringlens.investigation_models import InvestigationConfig
from ringlens.investigator import investigate
from ringlens.models import canonical_json
from ringlens.schema import initialize_schema
from ringlens.verify import read_history, read_state, verify_graph

REGISTRY_PATH = Path(__file__).resolve().parents[2] / ".ringlens" / "datasets.json"
MAX_SCAN_SEEDS = 50


def available_graphs(path: Path = REGISTRY_PATH) -> list[str]:
    """Return only server-issued names plus the configured legacy graph."""
    default = Settings().graph_name
    if not path.exists():
        return [default]
    try:
        data = json.loads(path.read_text("utf-8"))
        names = data["graphs"]
        if not isinstance(names, list) or any(
            not isinstance(name, str) or not name.startswith("ringlens_ui_") for name in names
        ):
            raise ValueError
    except (ValueError, OSError, KeyError):
        raise ValueError("Local dataset registry is invalid; repair it before importing.") from None
    return [default, *sorted(set(names) - {default})]


def register_graph(name: str, path: Path = REGISTRY_PATH) -> None:
    if not re.fullmatch(r"ringlens_ui_[0-9a-f]{32}", name):
        raise ValueError("Only server-issued UI graph names can be registered.")
    names = available_graphs(path)
    if name in names:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as file:
            file.write(canonical_json({"graphs": sorted([*names[1:], name])}) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def new_graph_name() -> str:
    return "ringlens_ui_" + uuid4().hex


def selected_store(graph_name: str, *, path: Path = REGISTRY_PATH) -> GraphStore:
    if graph_name not in available_graphs(path):
        raise ValueError("Dataset is not in the server-controlled registry.")
    return GraphStore(Settings(graph_name=graph_name))


def import_validated_csv(
    validation: CSVValidation,
    sources,
    *,
    mode: str,
    as_of: int,
    graph_name: str,
    revision: str = "",
    source_timezone: str | None = None,
    registry_path: Path = REGISTRY_PATH,
) -> ImportResult:
    """Revalidate bytes/settings/revision immediately before the single-writer import."""
    if not validation.valid or validation.batch is None:
        raise ValueError("Resolve blocking CSV errors and validate before importing.")
    if mode == "base":
        registered = graph_name in available_graphs(registry_path)
        if not re.fullmatch(r"ringlens_ui_[0-9a-f]{32}", graph_name):
            raise ValueError("New graph name was not issued by the application.")
        store = GraphStore(Settings(graph_name=graph_name))
        try:
            if not registered and store.graph_exists():
                raise ValueError("Generated graph name already exists; request a new dataset.")
            checked = validate_csv_sources(
                sources,
                mode=mode,
                as_of=as_of,
                store=store,
                graph=graph_name,
                revision=revision,
                source_timezone=source_timezone,
            )
            if not checked.valid or checked.fingerprint != validation.fingerprint:
                raise ValueError("Files, mappings, or settings changed; validate again.")
            # Register before the first database write so failed/LOADING imports
            # remain visible and can be retried with their original batch.
            if not registered:
                register_graph(graph_name, registry_path)
            initialize_schema(store)
            return ingest_batch(store, checked.batch)
        finally:
            store.close()
    with selected_store(graph_name, path=registry_path) as store:
        current = dataset_status(store)
        if current["revision"] != revision:
            raise ValueError("Dataset revision changed; validate against current data again.")
        checked = validate_csv_sources(
            sources,
            mode=mode,
            as_of=as_of,
            store=store,
            graph=graph_name,
            revision=revision,
            source_timezone=source_timezone,
        )
        if not checked.valid or checked.fingerprint != validation.fingerprint:
            raise ValueError("Files, mappings, or settings changed; validate again.")
        return ingest_batch(store, checked.batch)


def dataset_status(store: GraphStore) -> dict:
    if not store.graph_exists():
        return {"dataset_id": None, "status": "EMPTY", "as_of": None, "revision": "", "batches": []}
    dataset, history = read_history(read_state(store))
    if dataset is None:
        return {"dataset_id": None, "status": "EMPTY", "as_of": None, "revision": "", "batches": []}
    successful = [
        {
            "id": item.batch.id,
            "hash": item.batch.content_hash,
            "as_of": item.manifest.as_of,
            "completed_at": item.batch.completed_at,
            "mode": item.batch.mode,
        }
        for item in history
        if item.batch.status == "READY"
    ]
    descriptor = {
        "dataset_id": dataset.id,
        "schema_version": dataset.schema_version,
        "as_of": dataset.as_of,
        "batches": sorted(
            [
                {
                    "id": batch["id"],
                    "content_hash": batch["hash"],
                    "mode": batch["mode"],
                    "as_of": batch["as_of"],
                }
                for batch in successful
            ],
            key=lambda batch: batch["id"],
        ),
    }
    revision = (
        sha256(canonical_json(descriptor).encode()).hexdigest() if dataset.status == "READY" else ""
    )
    return {
        "dataset_id": dataset.id,
        "status": dataset.status,
        "as_of": dataset.as_of,
        "revision": revision,
        "batches": [
            {
                "id": item.batch.id,
                "mode": item.batch.mode,
                "status": item.batch.status,
                "started_at": item.batch.started_at,
                "completed_at": item.batch.completed_at,
                "error": item.batch.error,
                "as_of": item.manifest.as_of,
            }
            for item in sorted(history, key=lambda item: item.batch.started_at)
        ],
    }


def overview(store: GraphStore, *, window_days: int = 30) -> dict:
    status = dataset_status(store)
    if status["status"] != "READY":
        return {"state": status, "ready": False}
    report = verify_graph(store)
    if not report.ok:
        return {"state": status, "ready": False, "issues": report.issues}
    end = status["as_of"]
    start = max(0, end - window_days * 86400)
    parameters = {"start": start, "end": end}
    orders = store.read(
        "MATCH (o:Order) WHERE o.placed_at >= $start AND o.placed_at <= $end "
        "RETURN count(DISTINCT o.id)",
        parameters,
    ).result_set[0][0]
    refund_rows = store.read(
        "MATCH (r:Refund)-[:FOR_ORDER]->(o:Order) "
        "WHERE r.requested_at >= $start AND r.requested_at <= $end "
        "AND o.placed_at >= $start AND o.placed_at <= $end "
        "RETURN r.id, r.requested_at, r.amount_minor, o.id",
        parameters,
    ).result_set
    amount = sum(row[2] for row in refund_rows)
    activity = Counter(
        datetime.fromtimestamp(row[1], tz=UTC).date().isoformat() for row in refund_rows
    )
    case_list = cases.list_cases(store, limit=100)
    case_counts = Counter(
        (item.investigation.priority, item.investigation.status) for item in case_list.cases
    )
    return {
        "state": status,
        "ready": True,
        "counts": report.counts,
        "window_start": start,
        "window_end": end,
        "order_count": orders,
        "refunded_orders": len({row[3] for row in refund_rows}),
        "refunded_order_percent": round(100 * len({row[3] for row in refund_rows}) / orders, 2)
        if orders
        else 0,
        "refund_amount_minor": amount,
        "refund_activity": [{"date": day, "refunds": activity[day]} for day in sorted(activity)],
        "cases": [item.model_dump(mode="json") for item in case_list.cases],
        "case_counts": [
            {"priority": priority, "status": case_status, "cases": count}
            for (priority, case_status), count in sorted(case_counts.items())
        ],
        "case_list_truncated": case_list.truncated,
        "last_import": max(
            (b for b in status["batches"] if b["status"] == "READY"),
            key=lambda b: b["completed_at"],
            default=None,
        ),
    }


def refund_ids(store: GraphStore, *, as_of: int, window_days: int, limit: int = 500) -> list[str]:
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Refund listing limit must be 1..500.")
    rows = store.read(
        "MATCH (r:Refund) WHERE r.requested_at >= $start AND r.requested_at <= $end "
        "RETURN r.id ORDER BY r.id LIMIT $limit",
        {"start": max(0, as_of - window_days * 86400), "end": as_of, "limit": limit},
    ).result_set
    return [row[0] for row in rows]


def scan_refunds(
    store: GraphStore,
    *,
    as_of: int,
    window_days: int = 30,
    max_seeds: int = MAX_SCAN_SEEDS,
    progress=None,
) -> dict:
    """Bounded deterministic graph scan; no case writes or LLM calls."""
    if type(max_seeds) is not int or not 1 <= max_seeds <= MAX_SCAN_SEEDS:
        raise ValueError(f"Scan limit must be 1..{MAX_SCAN_SEEDS} refund seeds.")
    state = dataset_status(store)
    if state["status"] != "READY" or as_of > state["as_of"]:
        raise ValueError("Scan requires a READY dataset and an available assessment horizon.")
    parameters = {"start": max(0, as_of - window_days * 86400), "end": as_of}
    available = store.read(
        "MATCH (r:Refund) WHERE r.requested_at >= $start AND r.requested_at <= $end "
        "RETURN count(DISTINCT r.id)",
        parameters,
    ).result_set[0][0]
    selected = [
        row[0]
        for row in store.read(
            "MATCH (r:Refund) WHERE r.requested_at >= $start AND r.requested_at <= $end "
            "RETURN DISTINCT r.id ORDER BY r.id LIMIT $limit",
            {**parameters, "limit": max_seeds},
        ).result_set
    ]
    findings, failed = [], []
    for index, refund_id in enumerate(selected, 1):
        try:
            result = investigate(
                store, refund_id, as_of=as_of, config=InvestigationConfig(window_days=window_days)
            )
            if result.completeness != "COMPLETE":
                failed.append({"refund_id": refund_id, "error": [e.message for e in result.errors]})
            else:
                findings.append(
                    {
                        "seed_refund": refund_id,
                        "candidate_accounts": len(result.candidates),
                        "priority": result.priority,
                        "score": result.total_score,
                        "rules": ", ".join(f.evidence.rule_id for f in result.triggered_rules),
                        "as_of": result.assessment_as_of,
                    }
                )
        except Exception:
            failed.append(
                {"refund_id": refund_id, "error": ["Investigation failed; inspect graph health."]}
            )
        if progress:
            progress(index, len(selected))
    return {
        "findings": findings,
        "failed": failed,
        "assessed": len(selected),
        "available": available,
        "skipped": available - len(selected),
        "complete": not failed and len(selected) == available,
        "revision": state["revision"],
        "as_of": as_of,
        "window_days": window_days,
    }
