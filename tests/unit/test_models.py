import json

import pytest
from pydantic import ValidationError

from ringlens.models import (
    INT64_MAX,
    Account,
    Address,
    Dataset,
    Device,
    Evidence,
    ImportBatch,
    Investigation,
    Manifest,
    ManifestFile,
    Order,
    Refund,
    Review,
    evidence_details_json,
)

SOURCE_SAMPLES = [
    (Account, {"id": "acct_1", "created_at": 100}),
    (Device, {"id": "dev_1"}),
    (Address, {"id": "addr_1", "label": "Synthetic address A01"}),
    (
        Order,
        {
            "id": "ord_1",
            "account_id": "acct_1",
            "device_id": "dev_1",
            "address_id": "addr_1",
            "placed_at": 200,
            "amount_minor": 10000,
            "currency": "INR",
        },
    ),
    (
        Refund,
        {
            "id": "ref_1",
            "account_id": "acct_1",
            "order_id": "ord_1",
            "requested_at": 300,
            "amount_minor": 5000,
            "currency": "INR",
        },
    ),
]


@pytest.mark.parametrize(("model", "sample"), SOURCE_SAMPLES)
def test_valid_sources_and_exact_fields(model, sample):
    assert model.model_validate(sample).model_dump() == sample
    assert model.model_validate_json(json.dumps(sample)).model_dump() == sample
    with pytest.raises(ValidationError, match="Extra inputs"):
        model.model_validate({**sample, "scenario": "planted-label"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        model.model_validate({**sample, "source_hash": "0" * 64})


@pytest.mark.parametrize(("model", "sample"), SOURCE_SAMPLES)
@pytest.mark.parametrize("invalid", ["", "wrong_1", "acct_", "acct_a b", 1, True])
def test_bad_ids(model, sample, invalid):
    with pytest.raises(ValidationError):
        model.model_validate({**sample, "id": invalid})


@pytest.mark.parametrize("invalid", [True, False, "100", 1.0, -1, None, INT64_MAX + 1])
def test_timestamp_strictness(invalid):
    with pytest.raises(ValidationError):
        Account(id="acct_1", created_at=invalid)
    with pytest.raises(ValidationError):
        Account.model_validate_json(json.dumps({"id": "acct_1", "created_at": invalid}))


@pytest.mark.parametrize("invalid", [True, "100", 1.0, 0, -1, None, INT64_MAX + 1])
@pytest.mark.parametrize(("model", "sample"), SOURCE_SAMPLES[3:])
def test_amount_strictness(model, sample, invalid):
    with pytest.raises(ValidationError):
        model.model_validate({**sample, "amount_minor": invalid})


@pytest.mark.parametrize(("model", "sample"), SOURCE_SAMPLES[3:])
def test_only_inr(model, sample):
    with pytest.raises(ValidationError):
        model.model_validate({**sample, "currency": "USD"})


def test_address_is_explicitly_synthetic():
    with pytest.raises(ValidationError):
        Address(id="addr_1", label="221B Baker Street")


def investigation(**changes):
    return Investigation.model_validate(
        {
            "id": "inv_1",
            "status": "OPEN",
            "priority": "LOW",
            "score": 0,
            "rule_version": "v1",
            "assessment_as_of": 300,
            "created_at": 400,
            "updated_at": 400,
            **changes,
        }
    )


def evidence(**changes):
    return Evidence.model_validate(
        {
            "id": "ev_1",
            "rule_id": "test_rule",
            "rule_version": "v1",
            "window_start": 100,
            "window_end": 300,
            "contribution": 10,
            "source_ids": ["acct_1", "ord_1", "ref_1"],
            "details_json": '{"nested":{"b":2,"a":[1,null,true]}}',
            **changes,
        }
    )


def test_case_models_and_json_graph_properties():
    assert investigation(score=100).score == 100
    result = evidence()
    assert result.details_json == '{"nested":{"a":[1,null,true],"b":2}}'
    assert isinstance(result.model_dump()["details_json"], str)
    assert evidence_details_json({"message": "काल्पनिक", "counts": [1, 2]}) == (
        '{"counts":[1,2],"message":"काल्पनिक"}'
    )
    Review(id="rev_1", outcome="BENIGN_EXPLANATION", explanation="Synthetic test", reviewed_at=500)
    with pytest.raises(ValidationError):
        Account(id="acct_1", created_at=100, status="CONFIRMED_ABUSE")


@pytest.mark.parametrize("score", [-1, 101, "50", True, float("nan"), float("inf")])
def test_invalid_investigation_score(score):
    with pytest.raises(ValidationError):
        investigation(score=score)


@pytest.mark.parametrize(
    "details",
    [
        {},
        "not json",
        "[]",
        "null",
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":1e999}',
        '{"x":1,"x":2}',
        '{"x":{"a":1,"a":2}}',
        '{"x":"\\ud800"}',
    ],
)
def test_invalid_evidence_details(details):
    with pytest.raises(ValidationError):
        evidence(details_json=details)


@pytest.mark.parametrize("details", [{1: "invalid key"}, {"x": (1, 2)}, {"x": float("inf")}])
def test_details_encoder_does_not_coerce_non_json_values(details):
    with pytest.raises(ValueError):
        evidence_details_json(details)


@pytest.mark.parametrize(
    "changes",
    [
        {"window_end": 0},
        {"source_ids": []},
        {"source_ids": ["acct_1", "acct_1"]},
        {"source_ids": ["inv_1"]},
        {"contribution": True},
        {"contribution": float("nan")},
    ],
)
def test_invalid_evidence(changes):
    with pytest.raises(ValidationError):
        evidence(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "APPROVED"},
        {"priority": "URGENT"},
        {"updated_at": 399},
    ],
)
def test_invalid_investigation(changes):
    with pytest.raises(ValidationError):
        investigation(**changes)


def test_import_metadata_lifecycle():
    Dataset(id="ringlens_demo_v1", schema_version="1.0", as_of=300, status="INGESTING")
    loading = {
        "id": "batch_" + "0" * 64,
        "dataset_id": "ringlens_demo_v1",
        "content_hash": "0" * 64,
        "mode": "base",
        "status": "LOADING",
        "started_at": 1000,
        "completed_at": None,
        "error": None,
    }
    assert ImportBatch(**loading).completed_at is None
    ImportBatch(**{**loading, "status": "READY", "completed_at": 1001})
    ImportBatch(**{**loading, "status": "FAILED", "completed_at": 1001, "error": "Read failed"})
    for changes in (
        {"status": "READY"},
        {"error": "failed"},
        {"completed_at": 1001},
        {"status": "FAILED", "completed_at": 999, "error": "failed"},
        {"content_hash": "1" * 64},
    ):
        with pytest.raises(ValidationError):
            ImportBatch(**{**loading, **changes})


def test_manifest_paths_and_exact_file_set():
    valid = {"kind": "accounts", "path": "accounts.jsonl", "count": 0, "sha256": "0" * 64}
    ManifestFile(**valid)
    for changes in (
        {"path": "../accounts.jsonl"},
        {"path": "/accounts.jsonl"},
        {"count": True},
        {"sha256": "invalid"},
        {"kind": "evidence"},
    ):
        with pytest.raises(ValidationError):
            ManifestFile(**{**valid, **changes})
    with pytest.raises(ValidationError, match="files must contain"):
        Manifest(
            schema_version="1.0",
            dataset_id="ringlens_demo_v1",
            batch_id="batch_" + "0" * 64,
            mode="base",
            seed=1,
            as_of=300,
            files=[],
        )
