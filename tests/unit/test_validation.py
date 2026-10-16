from hashlib import sha256

import pytest
from pydantic import ValidationError

from ringlens.models import FILE_KINDS, Manifest, canonical_json
from ringlens.schema import source_node_properties
from ringlens.validation import (
    RecordValidationError,
    SourceRecords,
    canonical_record_bytes,
    manifest_content_hash,
    source_hash,
    validate_base_batch,
    validate_manifest,
    validate_records,
)


@pytest.fixture
def source_data():
    return {
        "accounts": [{"id": "acct_1", "created_at": 100}, {"id": "acct_2", "created_at": 100}],
        "devices": [{"id": "dev_1"}],
        "addresses": [{"id": "addr_1", "label": "Synthetic address A01"}],
        "orders": [
            {
                "id": "ord_1",
                "account_id": "acct_1",
                "device_id": "dev_1",
                "address_id": "addr_1",
                "placed_at": 200,
                "amount_minor": 10000,
                "currency": "INR",
            }
        ],
        "refunds": [
            {
                "id": "ref_1",
                "account_id": "acct_1",
                "order_id": "ord_1",
                "requested_at": 300,
                "amount_minor": 5000,
                "currency": "INR",
            }
        ],
    }


def manifest_for(records, **changes):
    files = []
    for kind in FILE_KINDS:
        rows = sorted(getattr(records, kind), key=lambda record: record.id)
        data = b"".join(canonical_record_bytes(record) for record in rows)
        files.append(
            {
                "kind": kind,
                "path": f"{kind}.jsonl",
                "count": len(rows),
                "sha256": sha256(data).hexdigest(),
            }
        )
    manifest = Manifest(
        schema_version="1.0",
        dataset_id="ringlens_demo_v1",
        batch_id="batch_" + "0" * 64,
        mode="base",
        seed=42,
        as_of=300,
        files=files,
    )
    descriptor = {**manifest.model_dump(), **changes}
    manifest = Manifest.model_validate(descriptor)
    return Manifest.model_validate(
        {**descriptor, "batch_id": f"batch_{manifest_content_hash(manifest)}"}
    )


def test_valid_base_and_equal_time_amount_boundaries(source_data):
    source_data["orders"][0]["placed_at"] = 100
    source_data["refunds"][0].update(requested_at=100, amount_minor=10000)
    records = SourceRecords.model_validate(source_data)
    validate_base_batch(records, manifest_for(records, as_of=100))


@pytest.mark.parametrize("kind", FILE_KINDS)
def test_duplicate_ids_are_rejected_even_for_identical_rows(source_data, kind):
    source_data[kind].append(source_data[kind][0].copy())
    with pytest.raises(RecordValidationError, match="duplicate ID"):
        validate_records(SourceRecords.model_validate(source_data), as_of=300)


@pytest.mark.parametrize(
    ("kind", "field", "value", "reason"),
    [
        ("orders", "account_id", "acct_missing", "orphan account_id"),
        ("orders", "device_id", "dev_missing", "orphan device_id"),
        ("orders", "address_id", "addr_missing", "orphan address_id"),
        ("refunds", "account_id", "acct_missing", "orphan account_id"),
        ("refunds", "order_id", "ord_missing", "orphan order_id"),
        ("refunds", "account_id", "acct_2", "owning account"),
        ("refunds", "requested_at", 199, "after order placement"),
        ("refunds", "amount_minor", 10001, "exceeds order amount"),
        ("accounts", "created_at", 201, "account creation"),
        ("accounts", "created_at", 301, "exceeds manifest as_of"),
        ("orders", "placed_at", 301, "exceeds manifest as_of"),
        ("refunds", "requested_at", 301, "exceeds manifest as_of"),
    ],
)
def test_cross_record_errors(source_data, kind, field, value, reason):
    source_data[kind][0][field] = value
    records = SourceRecords.model_validate(source_data)  # Individual records remain valid.
    with pytest.raises(RecordValidationError, match=reason):
        validate_records(records, as_of=300)


def test_duplicate_refunds_for_order(source_data):
    source_data["refunds"].append({**source_data["refunds"][0], "id": "ref_2"})
    with pytest.raises(RecordValidationError, match="multiple refunds for order"):
        validate_records(SourceRecords.model_validate(source_data), as_of=300)


def test_currency_mismatch_and_bypassed_model_validation_are_rejected(source_data):
    records = SourceRecords.model_validate(source_data)
    forged_refund = records.refunds[0].model_copy(update={"currency": "USD"})
    forged_records = records.model_copy(update={"refunds": [forged_refund]})
    with pytest.raises(ValidationError, match="INR"):
        validate_records(forged_records, as_of=300)
    with pytest.raises(ValidationError):
        validate_records(records, as_of=True)


def test_canonical_source_hash_and_graph_mapping(source_data):
    records = SourceRecords.model_validate(source_data)
    account = records.accounts[0]
    assert canonical_record_bytes(account) == b'{"created_at":100,"id":"acct_1"}\n'
    assert source_hash(account) == sha256(b'{"created_at":100,"id":"acct_1"}\n').hexdigest()
    order = records.orders[0]
    properties = source_node_properties(order)
    assert set(properties) == {"id", "placed_at", "amount_minor", "currency", "source_hash"}
    changed = order.model_copy(update={"device_id": "dev_2"})
    assert source_hash(order) != source_hash(changed)  # Edge references are immutable too.


def test_manifest_hash_is_not_circular_and_detects_tampering(source_data):
    records = SourceRecords.model_validate(source_data)
    manifest = manifest_for(records)
    expected = sha256(
        canonical_json(manifest.model_dump(exclude={"batch_id"})).encode()
    ).hexdigest()
    assert manifest.batch_id == f"batch_{expected}"
    validate_manifest(manifest)
    other_id = manifest.model_copy(update={"batch_id": "batch_" + "f" * 64})
    assert manifest_content_hash(other_id) == expected
    with pytest.raises(ValueError, match="content hash"):
        validate_manifest(other_id)
    for field, value in (("as_of", 301), ("seed", 43), ("mode", "append")):
        assert manifest_content_hash(manifest.model_copy(update={field: value})) != expected


def test_base_count_mismatch_and_append_require_context(source_data):
    records = SourceRecords.model_validate(source_data)
    manifest = manifest_for(records)
    missing = records.model_copy(update={"accounts": [records.accounts[0]]})
    with pytest.raises(RecordValidationError, match="manifest count"):
        validate_base_batch(missing, manifest)
    with pytest.raises(ValueError, match="append needs existing graph state"):
        validate_base_batch(records, manifest_for(records, mode="append"))
