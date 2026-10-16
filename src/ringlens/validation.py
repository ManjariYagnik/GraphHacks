"""Pure input validation and hashing; no graph reads, writes, or discovery."""

from hashlib import sha256

from pydantic import Field, TypeAdapter

from ringlens.models import (
    FILE_KINDS,
    Account,
    Address,
    Device,
    Manifest,
    Order,
    Refund,
    SourceRecord,
    StrictRecord,
    Timestamp,
    canonical_json,
)


class SourceRecords(StrictRecord):
    """A candidate snapshot for validation, not a runtime graph substitute."""

    accounts: list[Account] = Field(default_factory=list)
    devices: list[Device] = Field(default_factory=list)
    addresses: list[Address] = Field(default_factory=list)
    orders: list[Order] = Field(default_factory=list)
    refunds: list[Refund] = Field(default_factory=list)


class RecordValidationError(ValueError):
    def __init__(self, issues: list[str]) -> None:
        self.issues = tuple(issues)
        super().__init__("Invalid source records:\n" + "\n".join(f"- {issue}" for issue in issues))


def validate_records(records: SourceRecords, *, as_of: int) -> None:
    """Validate a full candidate snapshot, including existing records for appends.

    Field validation is rerun to reject models forged via model_construct/copy.
    This is intentionally separate from individual record validation.
    """
    records = SourceRecords.model_validate(records)
    as_of = TypeAdapter(Timestamp).validate_python(as_of)
    issues: list[str] = []
    indexes = {}
    for kind in FILE_KINDS:
        index = {}
        for record in getattr(records, kind):
            if record.id in index:
                issues.append(f"{kind}: duplicate ID {record.id}")
            else:
                index[record.id] = record
        indexes[kind] = index

    for account in records.accounts:
        if account.created_at > as_of:
            issues.append(f"{account.id}: created_at exceeds manifest as_of")
    for order in records.orders:
        for field, kind in (
            ("account_id", "accounts"),
            ("device_id", "devices"),
            ("address_id", "addresses"),
        ):
            if getattr(order, field) not in indexes[kind]:
                issues.append(f"{order.id}: orphan {field} {getattr(order, field)}")
        account = indexes["accounts"].get(order.account_id)
        if account and account.created_at > order.placed_at:
            issues.append(f"{order.id}: account creation must precede or equal order placement")
        if order.placed_at > as_of:
            issues.append(f"{order.id}: placed_at exceeds manifest as_of")

    refunded_orders: set[str] = set()
    for refund in records.refunds:
        if refund.order_id in refunded_orders:
            issues.append(f"{refund.id}: multiple refunds for order {refund.order_id}")
        refunded_orders.add(refund.order_id)
        if refund.account_id not in indexes["accounts"]:
            issues.append(f"{refund.id}: orphan account_id {refund.account_id}")
        order = indexes["orders"].get(refund.order_id)
        if order is None:
            issues.append(f"{refund.id}: orphan order_id {refund.order_id}")
        else:
            if refund.account_id != order.account_id:
                issues.append(f"{refund.id}: account must match the order's owning account")
            if refund.requested_at < order.placed_at:
                issues.append(f"{refund.id}: requested_at must be on or after order placement")
            if refund.currency != order.currency:
                issues.append(f"{refund.id}: currency must match order currency")
            if refund.amount_minor > order.amount_minor:
                issues.append(f"{refund.id}: refund amount exceeds order amount")
        if refund.requested_at > as_of:
            issues.append(f"{refund.id}: requested_at exceeds manifest as_of")
    if issues:
        raise RecordValidationError(issues)


def canonical_record_bytes(record: SourceRecord) -> bytes:
    """One canonical UTF-8 JSONL row, including its final LF."""
    if type(record) not in (Account, Device, Address, Order, Refund):
        raise ValueError("expected an immutable source record")
    validated = type(record).model_validate(record)
    return (canonical_json(validated.model_dump(mode="json")) + "\n").encode("utf-8")


def source_hash(record: SourceRecord) -> str:
    """Hash the complete canonical source row, including foreign keys and LF."""
    return sha256(canonical_record_bytes(record)).hexdigest()


def manifest_content_hash(manifest: Manifest) -> str:
    """Hash the canonical manifest descriptor excluding batch_id and final LF."""
    manifest = Manifest.model_validate(manifest)
    descriptor = manifest.model_dump(mode="json", exclude={"batch_id"})
    return sha256(canonical_json(descriptor).encode("utf-8")).hexdigest()


def validate_manifest(manifest: Manifest) -> None:
    if manifest.batch_id != f"batch_{manifest_content_hash(manifest)}":
        raise ValueError("manifest batch_id does not match its content hash")


def validate_base_batch(records: SourceRecords, manifest: Manifest) -> None:
    """Validate a complete base batch's records, manifest identity, and row counts.

    The future file reader must additionally verify canonical file bytes and their
    SHA-256 digests. This function neither reads files nor imports records.
    """
    validate_manifest(manifest)
    if manifest.mode != "base":
        raise ValueError(
            "validate_base_batch requires mode=base; append needs existing graph state"
        )
    validate_records(records, as_of=manifest.as_of)
    issues = [
        f"{file.path}: manifest count does not match records"
        for file in manifest.files
        if file.count != len(getattr(records, file.kind))
    ]
    if issues:
        raise RecordValidationError(issues)
