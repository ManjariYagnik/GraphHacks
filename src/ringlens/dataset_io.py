"""Canonical fixture files, integrity checks, and validation without a database."""

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from ringlens.models import (
    DATASET_ID,
    FILE_KINDS,
    SCHEMA_VERSION,
    Account,
    Address,
    Device,
    ImportMode,
    Manifest,
    Order,
    Refund,
    canonical_json,
)
from ringlens.validation import (
    SourceRecords,
    canonical_record_bytes,
    manifest_content_hash,
    source_hash,
    validate_base_batch,
    validate_manifest,
    validate_records,
)

SOURCE_MODELS = dict(zip(FILE_KINDS, (Account, Device, Address, Order, Refund), strict=True))
BATCH_FILENAMES = frozenset({"manifest.json", *(f"{kind}.jsonl" for kind in FILE_KINDS)})


class DatasetIOError(ValueError):
    """Readable fixture integrity, context, or output-conflict error."""


@dataclass(frozen=True)
class DatasetBatch:
    records: SourceRecords
    manifest: Manifest


def make_batch(records: SourceRecords, *, seed: int, as_of: int, mode: ImportMode) -> DatasetBatch:
    """Derive a manifest from canonical rows, without reading or writing files."""
    records = SourceRecords.model_validate(records)
    files = []
    for kind in FILE_KINDS:
        rows = sorted(getattr(records, kind), key=lambda row: row.id)
        payload = b"".join(canonical_record_bytes(row) for row in rows)
        files.append(
            {
                "kind": kind,
                "path": f"{kind}.jsonl",
                "count": len(rows),
                "sha256": sha256(payload).hexdigest(),
            }
        )
    manifest = Manifest(
        schema_version=SCHEMA_VERSION,
        dataset_id=DATASET_ID,
        batch_id="batch_" + "0" * 64,
        mode=mode,
        seed=seed,
        as_of=as_of,
        files=files,
    )
    manifest = Manifest.model_validate(
        {**manifest.model_dump(), "batch_id": f"batch_{manifest_content_hash(manifest)}"}
    )
    return DatasetBatch(records, manifest)


def batch_bytes(batch: DatasetBatch) -> dict[str, bytes]:
    """Serialize the exact six-file contract; empty kinds have zero bytes."""
    payloads = {
        f"{kind}.jsonl": b"".join(
            canonical_record_bytes(row)
            for row in sorted(getattr(batch.records, kind), key=lambda row: row.id)
        )
        for kind in FILE_KINDS
    }
    payloads["manifest.json"] = (
        canonical_json(batch.manifest.model_dump(mode="json")) + "\n"
    ).encode("utf-8")
    return payloads


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DatasetIOError("duplicate JSON object key")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise DatasetIOError("non-finite JSON number")


def _parse_json(payload: bytes, location: str) -> object:
    try:
        return json.loads(
            payload.decode("utf-8"), object_pairs_hook=_unique_keys, parse_constant=_nonfinite
        )
    except (ValueError, UnicodeError, RecursionError):
        raise DatasetIOError(f"{location}: invalid UTF-8 JSON or duplicate object keys") from None


def _read_file(directory: Path, filename: str) -> bytes:
    target = directory / filename
    if target.is_symlink():
        raise DatasetIOError(f"{filename}: symbolic links are not allowed in a fixture batch")
    try:
        return target.read_bytes()
    except OSError:
        raise DatasetIOError(
            f"{filename}: cannot read file; check that the batch is complete"
        ) from None


def read_batch(directory: Path) -> DatasetBatch:
    """Verify file bytes and individual models; references need validate_batch()."""
    directory = Path(directory)
    try:
        filenames = {entry.name for entry in directory.iterdir()}
    except OSError:
        raise DatasetIOError(
            "Cannot read input directory; supply a complete batch directory"
        ) from None
    if filenames != BATCH_FILENAMES:
        raise DatasetIOError(
            "Batch must contain exactly manifest.json and the five canonical JSONL files"
        )
    payload = _read_file(directory, "manifest.json")
    try:
        manifest = Manifest.model_validate(_parse_json(payload, "manifest.json"))
    except ValidationError:
        raise DatasetIOError(
            "manifest.json: invalid manifest fields or file declarations"
        ) from None
    if payload != (canonical_json(manifest.model_dump(mode="json")) + "\n").encode("utf-8"):
        raise DatasetIOError("manifest.json: expected canonical UTF-8 JSON with one final LF")
    validate_manifest(manifest)
    records = {}
    for file in manifest.files:
        data = _read_file(directory, file.path)
        if sha256(data).hexdigest() != file.sha256:
            raise DatasetIOError(f"{file.path}: SHA-256 mismatch; regenerate or restore the file")
        rows = []
        previous_id = None
        for number, line in enumerate(data.splitlines(keepends=True), start=1):
            location = f"{file.path}:{number}"
            try:
                row = SOURCE_MODELS[file.kind].model_validate(_parse_json(line, location))
            except ValidationError as error:
                issue = error.errors(include_input=False, include_context=False, include_url=False)[
                    0
                ]
                raise DatasetIOError(f"{location}: invalid record: {issue['msg']}") from None
            if line != canonical_record_bytes(row):
                raise DatasetIOError(f"{location}: expected canonical JSONL with one final LF")
            if previous_id is not None and row.id <= previous_id:
                raise DatasetIOError(
                    f"{location}: IDs must be unique and sorted in ascending order"
                )
            previous_id = row.id
            rows.append(row)
        if len(rows) != file.count:
            raise DatasetIOError(f"{file.path}: row count differs from manifest")
        records[file.kind] = rows
    return DatasetBatch(SourceRecords.model_validate(records), manifest)


def validate_batch(batch: DatasetBatch, *, base: DatasetBatch | None = None) -> SourceRecords:
    """Validate references and chronology, resolving an append against a base.

    Returns a validated candidate snapshot only. It performs no ingestion or graph
    discovery. Identical immutable references are no-op replays; conflicts fail.
    """
    validate_manifest(batch.manifest)
    for file in batch.manifest.files:
        if len(getattr(batch.records, file.kind)) != file.count:
            raise DatasetIOError(f"{file.path}: row count differs from manifest")
    if batch.manifest.mode == "base":
        if base is not None:
            raise DatasetIOError("A base batch does not take a --base context")
        validate_base_batch(batch.records, batch.manifest)
        return batch.records
    if base is None:
        raise DatasetIOError("Append validation requires --base pointing to a base dataset")
    if base.manifest.mode != "base":
        raise DatasetIOError("--base must point to a mode=base dataset")
    validate_base_batch(base.records, base.manifest)
    if batch.manifest.as_of < base.manifest.as_of:
        raise DatasetIOError("Append as_of must not move the base assessment horizon backward")
    merged = {}
    for kind in FILE_KINDS:
        rows = {row.id: row for row in getattr(base.records, kind)}
        seen = set()
        for row in getattr(batch.records, kind):
            if row.id in seen:
                raise DatasetIOError(f"{kind}: duplicate ID inside append batch")
            seen.add(row.id)
            if row.id in rows and source_hash(row) != source_hash(rows[row.id]):
                raise DatasetIOError(f"{kind}: conflicting reuse of immutable ID {row.id}")
            rows[row.id] = row
        merged[kind] = list(rows.values())
    snapshot = SourceRecords.model_validate(merged)
    validate_records(snapshot, as_of=batch.manifest.as_of)
    return snapshot


def load_dataset(directory: Path, *, base_directory: Path | None = None) -> DatasetBatch:
    batch = read_batch(directory)
    base = read_batch(base_directory) if base_directory is not None else None
    validate_batch(batch, base=base)
    return batch


def write_outputs(outputs: dict[Path, bytes], *, overwrite: bool = False) -> None:
    """Check all output conflicts before writes; identical reruns preserve files."""
    pending = []
    for target, payload in outputs.items():
        if target.is_symlink():
            raise DatasetIOError("Refusing to replace a symbolic-link output")
        if target.exists():
            if not target.is_file():
                raise DatasetIOError("An output path already exists and is not a regular file")
            if target.read_bytes() == payload:
                continue
            if not overwrite:
                raise DatasetIOError(
                    "Existing output differs; choose another output path or pass --overwrite"
                )
        pending.append((target, payload))
    for target, payload in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
