"""Validated, resumable single-writer ingestion into the authoritative FalkorDB graph."""

import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel

from ringlens import import_queries as q
from ringlens.dataset_io import DatasetBatch, batch_bytes, read_batch
from ringlens.graph_store import GraphStore, GraphStoreError
from ringlens.models import canonical_json
from ringlens.schema import source_node_properties
from ringlens.validation import (
    SourceRecords,
    manifest_content_hash,
    source_hash,
    validate_manifest,
    validate_records,
)
from ringlens.verify import (
    GraphIntegrityError,
    VerificationReport,
    graph_records,
    read_history,
    read_state,
    verify_graph,
    verify_history_content,
)


class IngestionError(ValueError):
    """Validation, recovery, or import failure that is safe to display."""


@dataclass(frozen=True)
class ImportPlan:
    records: SourceRecords
    no_op: bool
    as_of: int


class ImportResult(BaseModel):
    batch_id: str
    status: str
    counts: dict[str, int]
    as_of: int
    verification: VerificationReport | None = None


def prepare_import(store: GraphStore, batch: DatasetBatch) -> ImportPlan:
    manifest = batch.manifest
    # Validate programmatic batches too; a caller cannot bypass pre-write content
    # validation by constructing DatasetBatch directly rather than reading files.
    validate_manifest(manifest)
    SourceRecords.model_validate(batch.records)
    payloads = batch_bytes(batch)
    for file in manifest.files:
        if (
            len(getattr(batch.records, file.kind)) != file.count
            or sha256(payloads[file.path]).hexdigest() != file.sha256
        ):
            raise IngestionError("Incoming source content/counts do not match the manifest.")
    if manifest.mode == "base":
        validate_records(batch.records, as_of=manifest.as_of)
    state = read_state(store)
    if state.datasets and any(
        props.get("id") != manifest.dataset_id for _, props in state.datasets
    ):
        raise IngestionError("Graph is already assigned to a different dataset_id.")
    dataset, history = read_history(state)
    current = next((item for item in history if item.batch.id == manifest.batch_id), None)
    unfinished = [item for item in history if item.batch.status != "READY"]
    if any(item.batch.id != manifest.batch_id for item in unfinished):
        raise IngestionError(
            "A different import is unfinished; retry its original batch before another import."
        )
    if current and (
        current.manifest != manifest
        or set(current.source_ids)
        != {row.id for kind in q.SOURCE_LABELS for row in getattr(batch.records, kind)}
    ):
        raise IngestionError("Existing batch identity is associated with different content.")
    if dataset is None:
        if any(state.nodes.values()):
            raise IngestionError(
                "Graph contains unmanaged source nodes; refusing to adopt or overwrite them."
            )
        if manifest.mode != "base":
            raise IngestionError("Append requires an existing successfully imported base dataset.")
    else:
        if not history:
            raise IngestionError("Dataset has no import history; explicit recovery is required.")
        if current is None and manifest.mode == "base":
            raise IngestionError(
                "A base batch already exists; only an identical replay or append is allowed."
            )
        if current is None and dataset.status != "READY":
            raise IngestionError("Dataset is not READY; finish its previous import first.")
        if manifest.mode == "append" and (current is None or current.batch.status != "READY"):
            if not any(
                item.batch.mode == "base" and item.batch.status == "READY" for item in history
            ):
                raise IngestionError("Append requires a READY base batch.")
            if manifest.as_of <= dataset.as_of:
                raise IngestionError(
                    "A new append as_of must advance beyond the last successful horizon."
                )
    recovery = current is not None and current.batch.status in {"LOADING", "FAILED"}
    existing = graph_records(state, recovery_records=batch.records if recovery else None)
    combined = {}
    for kind in q.SOURCE_LABELS:
        indexed = {row.id: row for row in getattr(existing, kind)}
        for row in getattr(batch.records, kind):
            if row.id in indexed and source_hash(indexed[row.id]) != source_hash(row):
                raise IngestionError(
                    "Existing source ID is reused with different immutable content."
                )
            indexed[row.id] = row
        combined[kind] = list(indexed.values())
    records = SourceRecords.model_validate(combined)
    no_op = current is not None and current.batch.status == "READY"
    horizon = dataset.as_of if no_op else manifest.as_of
    validate_records(records, as_of=horizon)
    verify_history_content(history, records, extra=batch)
    if no_op:
        report = verify_graph(store)
        if not report.ok:
            raise IngestionError(
                "READY replay failed graph verification: " + "; ".join(report.issues)
            )
    return ImportPlan(records, no_op, horizon)


def ingest_batch(
    store: GraphStore, batch: DatasetBatch, *, dry_run: bool = False, batch_size: int = 50
) -> ImportResult:
    if type(batch_size) is not int or not 1 <= batch_size <= 250:
        raise ValueError("batch_size must be an integer in 1..250")
    plan = prepare_import(store, batch)

    def result(status, report=None):
        return ImportResult(
            batch_id=batch.manifest.batch_id,
            status=status,
            counts={kind: len(getattr(plan.records, kind)) for kind in q.SOURCE_LABELS},
            as_of=plan.as_of,
            verification=report,
        )

    if dry_run:
        return result("DRY_RUN_NO_OP" if plan.no_op else "DRY_RUN")
    if plan.no_op:
        return result("NO_OP")
    # Preflight is repeated under a fail-fast lease. No graph writes or lease keys
    # are created for invalid input or dry-run. READY replays never write anything.
    with store.import_lock() as renew:
        plan = prepare_import(store, batch)
        if plan.no_op:
            return result("NO_OP")
        manifest = batch.manifest
        identity = {"dataset_id": manifest.dataset_id, "batch_id": manifest.batch_id}
        started = False

        def write(query, params, expected_count):
            renew()
            rows = store.write(query, params).result_set
            if rows != [[expected_count]]:
                raise IngestionError(
                    "A write did not match every expected endpoint; import verification failed."
                )

        try:
            # Treat the metadata write as uncertain until proven otherwise, so an
            # acknowledgement failure can still be marked FAILED when connected.
            started = True
            write(
                q.START_BATCH,
                {
                    **identity,
                    "schema_version": manifest.schema_version,
                    "as_of": manifest.as_of,
                    "content_hash": manifest_content_hash(manifest),
                    "mode": manifest.mode,
                    "started_at": int(time.time()),
                    "manifest_json": canonical_json(manifest.model_dump(mode="json")),
                    "source_ids": sorted(
                        row.id for kind in q.SOURCE_LABELS for row in getattr(batch.records, kind)
                    ),
                },
                1,
            )
            for kind in q.SOURCE_LABELS:
                rows = [source_node_properties(row) for row in getattr(batch.records, kind)]
                for offset in range(0, len(rows), batch_size):
                    chunk = rows[offset : offset + batch_size]
                    write(q.WRITE_NODES[kind], {"rows": chunk}, len(chunk))
            for kind, relation, *_ in q.EDGE_SPECS:
                rows = [row.model_dump() for row in getattr(batch.records, kind)]
                for offset in range(0, len(rows), batch_size):
                    chunk = rows[offset : offset + batch_size]
                    write(q.WRITE_EDGES[relation], {"rows": chunk}, len(chunk))
            renew()
            report = verify_graph(store, expected=plan.records, pending_batch=manifest.batch_id)
            if not report.ok:
                raise IngestionError("Graph verification failed: " + "; ".join(report.issues))
            write(
                q.FINISH_BATCH,
                {**identity, "completed_at": int(time.time()), "as_of": manifest.as_of},
                1,
            )
            report = report.model_copy(
                update={
                    "as_of": manifest.as_of,
                    "dataset_status": "READY",
                    "batches": [{**item, "status": "READY"} for item in report.batches],
                }
            )
            return result("READY", report)
        except Exception as error:
            if started:
                try:
                    renew()
                    detail = (
                        str(error)
                        if isinstance(error, (GraphStoreError, IngestionError, GraphIntegrityError))
                        else "Unexpected failure; retry this original batch."
                    )
                    store.write(
                        q.FAIL_BATCH,
                        {
                            **identity,
                            "completed_at": int(time.time()),
                            "error": f"{type(error).__name__}: {detail}"[:800],
                        },
                    )
                except Exception:
                    # A lost connection/lease can leave LOADING, never READY.
                    pass
            if isinstance(error, (GraphStoreError, IngestionError)):
                raise
            raise IngestionError(
                "Import failed before completion; inspect import state and retry the same batch."
            ) from None


def ingest_directory(
    store: GraphStore, directory: Path, *, dry_run: bool = False, batch_size: int = 50
) -> ImportResult:
    return ingest_batch(store, read_batch(directory), dry_run=dry_run, batch_size=batch_size)
