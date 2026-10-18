"""Bounded CSV input boundary for the immutable RingLens source contract."""

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from ringlens.dataset_io import SOURCE_MODELS, DatasetBatch, make_batch, read_batch
from ringlens.ingest import prepare_import
from ringlens.models import FILE_KINDS, canonical_json
from ringlens.validation import RecordValidationError, SourceRecords, validate_records

MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 8_000_000
MAX_FILE_ROWS = 5_000
MAX_TOTAL_ROWS = 12_000
PREVIEW_ROWS = 8
TIME_FIELDS = frozenset({"created_at", "placed_at", "requested_at"})
INTEGER_FIELDS = frozenset({"amount_minor"})
ALIASES = {
    "id": ("entity_id", "record_id"),
    "account_id": ("customer_id",),
    "device_id": ("device",),
    "address_id": ("shipping_address_id",),
    "order_id": ("purchase_id",),
    "created_at": ("created", "account_created_at"),
    "placed_at": ("order_date", "ordered_at"),
    "requested_at": ("refund_date", "requested"),
    "amount_minor": ("amount_paise",),
    "currency": ("currency_code",),
    "label": ("address_label",),
}


class CSVLimits(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RINGLENS_CSV_", env_file=".env", extra="ignore")
    max_file_bytes: int = Field(default=MAX_FILE_BYTES, ge=1, le=50_000_000)
    max_total_bytes: int = Field(default=MAX_TOTAL_BYTES, ge=1, le=100_000_000)
    max_file_rows: int = Field(default=MAX_FILE_ROWS, ge=1, le=100_000)
    max_total_rows: int = Field(default=MAX_TOTAL_ROWS, ge=1, le=200_000)


@dataclass(frozen=True)
class CSVSource:
    filename: str
    kind: str
    content: bytes
    mapping: dict[str, str] | None = None
    ignored_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class CSVIssue:
    filename: str
    row: int
    column: str
    reason: str
    correction: str
    severity: str = "ERROR"

    def as_dict(self):
        return vars(self).copy()


@dataclass(frozen=True)
class CSVPreview:
    filename: str
    kind: str
    count: int
    rows: list[dict]


@dataclass(frozen=True)
class CSVValidation:
    batch: DatasetBatch | None
    issues: list[CSVIssue]
    previews: list[CSVPreview]
    fingerprint: str

    @property
    def valid(self):
        return self.batch is not None and not any(i.severity == "ERROR" for i in self.issues)


def required_columns(kind: str) -> tuple[str, ...]:
    if kind not in SOURCE_MODELS:
        raise ValueError("Unknown entity type; choose accounts/devices/addresses/orders/refunds.")
    return tuple(SOURCE_MODELS[kind].model_fields)


def suggested_kind(filename: str) -> str | None:
    stem = Path(filename).name.lower()
    return next((kind for kind in FILE_KINDS if stem == f"{kind}.csv"), None)


def suggested_mapping(headers: list[str], kind: str) -> dict[str, str]:
    result = {}
    for column in required_columns(kind):
        matches = [
            header for header in headers if header.lower() in (column, *ALIASES.get(column, ()))
        ]
        if len(matches) == 1:
            result[column] = matches[0]
    return result


def csv_headers(content: bytes, *, limits: CSVLimits | None = None) -> list[str]:
    limits = limits or CSVLimits()
    if len(content) > limits.max_file_bytes:
        raise ValueError(f"CSV exceeds the {limits.max_file_bytes:,}-byte per-file limit.")
    try:
        text = content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        headers = next(reader)
    except (UnicodeError, csv.Error, StopIteration):
        raise ValueError("CSV needs a valid UTF-8 header and well-formed quoting.") from None
    if not headers or any(not h or h != h.strip() for h in headers):
        raise ValueError("CSV headers must be nonempty and have no surrounding spaces.")
    if len(headers) != len(set(headers)):
        raise ValueError("Duplicate CSV headers are not allowed; rename repeated columns.")
    return headers


def _timestamp(value: str, source_timezone: str | None) -> int:
    if re.fullmatch(r"0|[1-9][0-9]*", value):
        return int(value)
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?", value):
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            if not source_timezone:
                raise ValueError("Timezone-free timestamp needs an explicit source timezone.")
            zone = ZoneInfo(source_timezone)
            parsed = parsed.replace(tzinfo=zone)
            # Reject DST gaps/overlaps: the local clock must map to one instant.
            for fold in (0, 1):
                instant = parsed.replace(fold=fold).astimezone(UTC)
                if instant.astimezone(zone).replace(tzinfo=None) != parsed.replace(tzinfo=None):
                    raise ValueError("Local timestamp falls in a daylight-saving gap.")
            if parsed.replace(fold=0).utcoffset() != parsed.replace(fold=1).utcoffset():
                raise ValueError("Ambiguous local timestamp; provide a UTC offset.")
        result = int(parsed.timestamp())
        if result < 0:
            raise ValueError
        return result
    except ZoneInfoNotFoundError:
        raise ValueError(
            "Unknown source timezone; use an IANA name such as Asia/Kolkata."
        ) from None
    except (OverflowError, OSError, ValueError) as error:
        if str(error).startswith(("Timezone-free", "Local timestamp", "Ambiguous local")):
            raise
        raise ValueError("Use Unix seconds or ISO 8601 with a UTC offset.") from None


def _convert(column: str, value: str, source_timezone: str | None):
    if column in TIME_FIELDS:
        return _timestamp(value, source_timezone)
    if column in INTEGER_FIELDS:
        if not re.fullmatch(r"0|[1-9][0-9]*", value):
            raise ValueError("Use positive integer paise, e.g. 12345 for INR 123.45.")
        return int(value)
    return value


def _issue(filename, row, column, reason, correction):
    return CSVIssue(filename, row, column, reason, correction)


def _parse(source: CSVSource, source_timezone: str | None, limits: CSVLimits):
    issues, rows, preview, locations = [], [], [], {}
    if source.kind not in FILE_KINDS:
        return (
            [],
            [],
            {},
            [
                _issue(
                    source.filename,
                    1,
                    "kind",
                    "Unknown entity type.",
                    "Select one of the five source kinds.",
                )
            ],
        )
    try:
        headers = csv_headers(source.content, limits=limits)
        reader = csv.reader(
            io.StringIO(source.content.decode("utf-8-sig"), newline=""), strict=True
        )
        next(reader)
    except ValueError as error:
        return (
            [],
            [],
            {},
            [_issue(source.filename, 1, "header", str(error), "Fix CSV encoding and headers.")],
        )
    columns = required_columns(source.kind)
    mapping = (
        source.mapping if source.mapping is not None else suggested_mapping(headers, source.kind)
    )
    if set(mapping) - set(columns) or len(set(mapping.values())) != len(mapping):
        issues.append(
            _issue(
                source.filename,
                1,
                "mapping",
                "Invalid or repeated target mapping.",
                "Map each field to a different CSV header.",
            )
        )
    for column in columns:
        if column not in mapping or mapping[column] not in headers:
            issues.append(
                _issue(
                    source.filename,
                    1,
                    column,
                    "Required column is unmapped.",
                    f"Map {column} to a CSV header.",
                )
            )
    unknown = set(headers) - set(mapping.values()) - set(source.ignored_columns)
    if unknown:
        issues.append(
            _issue(
                source.filename,
                1,
                "headers",
                "Extra columns are not explicitly ignored: " + ", ".join(sorted(unknown)),
                "Mark each extra header as ignored or map it.",
            )
        )
    if set(source.ignored_columns) - set(headers):
        issues.append(
            _issue(
                source.filename,
                1,
                "headers",
                "Ignored header does not exist.",
                "Update ignored-column selection.",
            )
        )
    if issues:
        return [], [], {}, issues
    seen = set()
    try:
        record_count = 0
        for values in reader:
            record_count += 1
            number = reader.line_num
            if record_count > limits.max_file_rows:
                issues.append(
                    _issue(
                        source.filename,
                        number,
                        "file",
                        "Row limit exceeded.",
                        f"Split the file below {limits.max_file_rows:,} data rows.",
                    )
                )
                break
            if len(values) != len(headers):
                issues.append(
                    _issue(
                        source.filename,
                        number,
                        "row",
                        "Column count differs from header.",
                        "Fix quoting or missing/extra cells.",
                    )
                )
                continue
            cells = dict(zip(headers, values, strict=True))
            values_by_field = {}
            bad = False
            for column in columns:
                try:
                    values_by_field[column] = _convert(
                        column, cells[mapping[column]], source_timezone
                    )
                except ValueError as error:
                    issues.append(
                        _issue(
                            source.filename, number, column, str(error), "Correct the cell value."
                        )
                    )
                    bad = True
            if bad:
                continue
            try:
                record = SOURCE_MODELS[source.kind].model_validate(values_by_field)
            except ValidationError as error:
                for detail in error.errors(
                    include_input=False, include_context=False, include_url=False
                ):
                    column = str(detail["loc"][0]) if detail["loc"] else "row"
                    issues.append(
                        _issue(
                            source.filename,
                            number,
                            column,
                            detail["msg"],
                            "Use the canonical ID prefix, INR, and required field format.",
                        )
                    )
                continue
            if record.id in seen:
                issues.append(
                    _issue(
                        source.filename,
                        number,
                        "id",
                        f"Duplicate {record.id} in this batch.",
                        "Give each source record one unique ID.",
                    )
                )
                continue
            seen.add(record.id)
            locations[record.id] = (source.filename, number)
            rows.append(record)
            if len(preview) < PREVIEW_ROWS:
                preview.append(record.model_dump(mode="json"))
    except csv.Error:
        issues.append(
            _issue(
                source.filename,
                reader.line_num,
                "row",
                "Malformed CSV quoting.",
                "Quote embedded commas and double quotes correctly.",
            )
        )
    return rows, preview, locations, issues


def input_fingerprint(sources, mode, as_of, graph, revision, source_timezone, limits=None):
    limits = limits or CSVLimits()
    descriptor = {
        "mode": mode,
        "as_of": as_of,
        "graph": graph,
        "revision": revision,
        "source_timezone": source_timezone,
        "limits": limits.model_dump(),
        "files": [
            {
                "filename": s.filename,
                "kind": s.kind,
                "sha256": sha256(s.content).hexdigest(),
                "mapping": s.mapping,
                "ignored": sorted(s.ignored_columns),
            }
            for s in sources
        ],
    }
    return sha256(canonical_json(descriptor).encode()).hexdigest()


def validate_csv_sources(
    sources: list[CSVSource],
    *,
    mode: str,
    as_of: int,
    store=None,
    graph: str = "",
    revision: str = "",
    source_timezone: str | None = None,
    limits: CSVLimits | None = None,
) -> CSVValidation:
    """Validate the whole bounded batch; optional graph preflight remains read-only."""
    limits = limits or CSVLimits()
    fingerprint = input_fingerprint(sources, mode, as_of, graph, revision, source_timezone, limits)
    issues, previews, locations = [], [], {}
    records = {kind: [] for kind in FILE_KINDS}
    if mode not in {"base", "append"}:
        issues.append(_issue("batch", 0, "mode", "Unknown mode.", "Choose base or append."))
    if not sources:
        issues.append(
            _issue(
                "batch",
                0,
                "files",
                "No CSV files selected.",
                "Upload one or more entity CSV files.",
            )
        )
    if sum(len(s.content) for s in sources) > limits.max_total_bytes:
        issues.append(
            _issue(
                "batch",
                0,
                "files",
                "Total upload byte limit exceeded.",
                f"Stay below {limits.max_total_bytes:,} bytes.",
            )
        )
    kinds = [s.kind for s in sources]
    if len(kinds) != len(set(kinds)):
        issues.append(
            _issue(
                "batch",
                0,
                "kind",
                "Multiple files selected for one entity type.",
                "Combine same-kind rows in one CSV file.",
            )
        )
    for source in sources:
        rows, preview, found, errors = _parse(source, source_timezone, limits)
        if source.kind in records:
            records[source.kind].extend(rows)
        locations.update(found)
        previews.append(CSVPreview(source.filename, source.kind, len(rows), preview))
        issues.extend(errors)
    if sum(len(rows) for rows in records.values()) > limits.max_total_rows:
        issues.append(
            _issue(
                "batch",
                0,
                "files",
                "Total row limit exceeded.",
                f"Stay below {limits.max_total_rows:,} rows.",
            )
        )
    if mode == "base" and (not records["accounts"] or not records["orders"]):
        issues.append(
            _issue(
                "batch",
                0,
                "files",
                "A new dataset needs accounts and orders.",
                "Upload referenced accounts, devices, addresses and orders; refunds may be empty.",
            )
        )
    if issues:
        return CSVValidation(None, issues, previews, fingerprint)
    snapshot = SourceRecords.model_validate(records)
    try:
        batch = make_batch(snapshot, seed=0, as_of=as_of, mode=mode)
        if mode == "base":
            validate_records(snapshot, as_of=as_of)
        if store is not None:
            prepare_import(store, batch)
        elif mode == "append":
            raise ValueError("Append validation requires a selected existing graph.")
    except (ValueError, ValidationError) as error:
        messages = error.issues if isinstance(error, RecordValidationError) else [str(error)]
        for message in messages:
            record_id = message.split(":", 1)[0]
            filename, row = locations.get(record_id, ("batch", 0))
            issues.append(
                _issue(
                    filename,
                    row,
                    "reference/time",
                    message,
                    "Check referenced IDs, chronology, currency, amount, and data-through time.",
                )
            )
        return CSVValidation(None, issues, previews, fingerprint)
    if not snapshot.refunds:
        issues.append(
            CSVIssue(
                "batch",
                0,
                "refunds",
                "No refunds in this batch; investigation needs a refund record.",
                "Append refunds later to investigate.",
                "WARNING",
            )
        )
    return CSVValidation(batch, issues, previews, fingerprint)


def _csv_bytes(kind, rows):
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    columns = required_columns(kind)
    writer.writerow(columns)
    for row in rows:
        values = row.model_dump()
        writer.writerow([values[column] for column in columns])
    return out.getvalue().encode("utf-8")


def csv_bundle(*, sample: bool = False, append: bool = False) -> bytes:
    """Create templates, a synthetic base bundle, or its refund-only append."""
    if append and not sample:
        raise ValueError("Append sample requires sample=True.")
    rows = (
        read_batch(
            Path(__file__).resolve().parents[2]
            / ("data/demo/new_evidence" if append else "data/demo/base")
        ).records
        if sample
        else SourceRecords()
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for kind in ("refunds",) if append else FILE_KINDS:
            archive.writestr(f"{kind}.csv", _csv_bytes(kind, getattr(rows, kind)))
    return output.getvalue()


def error_report(issues: list[CSVIssue]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("severity", "filename", "row", "column", "reason", "suggested_correction"))
    for issue in issues:
        # Prevent exported error text from becoming a spreadsheet formula.
        def safe(value):
            return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value

        writer.writerow(
            (
                issue.severity,
                safe(issue.filename),
                issue.row,
                safe(issue.column),
                safe(issue.reason),
                safe(issue.correction),
            )
        )
    return output.getvalue().encode("utf-8")
