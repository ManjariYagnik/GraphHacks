"""Strict records for schema 1.0; cross-record rules live in validation.py."""

import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

INT64_MAX = 2**63 - 1
SCHEMA_VERSION = "1.0"
DATASET_ID = "ringlens_demo_v1"
FILE_KINDS = ("accounts", "devices", "addresses", "orders", "refunds")

type Timestamp = Annotated[int, Field(strict=True, ge=0, le=INT64_MAX)]
type AmountMinor = Annotated[int, Field(strict=True, gt=0, le=INT64_MAX)]
type NonemptyString = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]
type AccountId = Annotated[str, Field(pattern=r"^acct_[A-Za-z0-9][A-Za-z0-9_-]*$")]
type DeviceId = Annotated[str, Field(pattern=r"^dev_[A-Za-z0-9][A-Za-z0-9_-]*$")]
type AddressId = Annotated[str, Field(pattern=r"^addr_[A-Za-z0-9][A-Za-z0-9_-]*$")]
type OrderId = Annotated[str, Field(pattern=r"^ord_[A-Za-z0-9][A-Za-z0-9_-]*$")]
type RefundId = Annotated[str, Field(pattern=r"^ref_[A-Za-z0-9][A-Za-z0-9_-]*$")]
type SourceId = AccountId | DeviceId | AddressId | OrderId | RefundId
type SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type BatchId = Annotated[str, Field(pattern=r"^batch_[0-9a-f]{64}$")]
type FileKind = Literal["accounts", "devices", "addresses", "orders", "refunds"]
type ImportMode = Literal["base", "append"]
type FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class StrictRecord(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )


class Account(StrictRecord):
    id: AccountId
    created_at: Timestamp


class Device(StrictRecord):
    id: DeviceId


class Address(StrictRecord):
    id: AddressId
    label: Annotated[str, Field(pattern=r"^Synthetic address [A-Za-z0-9][A-Za-z0-9_-]*$")]


class Order(StrictRecord):
    id: OrderId
    account_id: AccountId
    device_id: DeviceId
    address_id: AddressId
    placed_at: Timestamp
    amount_minor: AmountMinor
    currency: Literal["INR"]


class Refund(StrictRecord):
    id: RefundId
    account_id: AccountId
    order_id: OrderId
    requested_at: Timestamp
    amount_minor: AmountMinor
    currency: Literal["INR"]


type SourceRecord = Account | Device | Address | Order | Refund


class Investigation(StrictRecord):
    id: Annotated[str, Field(pattern=r"^inv_[A-Za-z0-9][A-Za-z0-9_-]*$")]
    status: Literal["OPEN", "IN_REVIEW", "CLOSED", "REOPENED"]
    priority: Literal["LOW", "MEDIUM", "HIGH"]
    score: Annotated[FiniteNumber, Field(ge=0, le=100)]
    rule_version: NonemptyString
    assessment_as_of: Timestamp
    created_at: Timestamp
    updated_at: Timestamp

    @model_validator(mode="after")
    def timestamps_in_order(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must be on or after created_at")
        return self


def _reject_json_constant(_: str) -> None:
    raise ValueError("JSON must not contain NaN or infinity")


def _unique_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON object keys must be unique")
        result[key] = value
    return result


def canonical_json(value: object) -> str:
    """Canonical JSON for validated data, without a trailing newline.

    Only actual JSON types are accepted; tuples, non-string map keys, and NaN
    must not be silently converted. Encoding checks reject unpaired surrogates.
    """

    def check(item: object) -> None:
        if item is None or type(item) in (str, int, bool, float):
            return
        if type(item) is list:
            for member in item:
                check(member)
        elif type(item) is dict and all(type(key) is str for key in item):
            for member in item.values():
                check(member)
        else:
            raise ValueError("expected JSON values with string object keys")

    try:
        check(value)
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("value must be finite, UTF-8 serializable JSON") from None


def evidence_details_json(details: dict[str, object]) -> str:
    """Explicitly encode a details object before assigning Evidence.details_json."""
    if type(details) is not dict:
        raise ValueError("evidence details must be a JSON object")
    return canonical_json(details)


class Evidence(StrictRecord):
    id: Annotated[str, Field(pattern=r"^ev_[A-Za-z0-9][A-Za-z0-9_-]*$")]
    rule_id: NonemptyString
    rule_version: NonemptyString
    window_start: Timestamp
    window_end: Timestamp
    contribution: FiniteNumber
    source_ids: Annotated[list[SourceId], Field(min_length=1)]
    details_json: str

    @field_validator("details_json")
    @classmethod
    def validated_json_string(cls, value: str) -> str:
        try:
            details = json.loads(
                value, parse_constant=_reject_json_constant, object_pairs_hook=_unique_json_keys
            )
            return evidence_details_json(details)
        except (ValueError, RecursionError):
            raise ValueError(
                "details_json must encode a finite JSON object with unique keys"
            ) from None

    @model_validator(mode="after")
    def valid_window_and_sources(self) -> Self:
        if self.window_end < self.window_start:
            raise ValueError("window_end must be on or after window_start")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source_ids must be unique")
        return self


class Review(StrictRecord):
    id: Annotated[str, Field(pattern=r"^rev_[A-Za-z0-9][A-Za-z0-9_-]*$")]
    outcome: Literal["BENIGN_EXPLANATION", "NEEDS_MORE_INFORMATION", "CONFIRMED_ABUSE"]
    explanation: NonemptyString
    reviewed_at: Timestamp


class Dataset(StrictRecord):
    id: Literal["ringlens_demo_v1"]
    schema_version: Literal["1.0"]
    as_of: Timestamp
    status: Literal["INGESTING", "READY", "FAILED"]


class ImportBatch(StrictRecord):
    id: BatchId
    dataset_id: Literal["ringlens_demo_v1"]
    content_hash: SHA256
    mode: ImportMode
    status: Literal["LOADING", "READY", "FAILED"]
    started_at: Timestamp
    completed_at: Timestamp | None
    error: NonemptyString | None

    @model_validator(mode="after")
    def valid_lifecycle(self) -> Self:
        if self.id != f"batch_{self.content_hash}":
            raise ValueError("id must be batch_ followed by content_hash")
        if self.status == "LOADING":
            if self.completed_at is not None or self.error is not None:
                raise ValueError("LOADING requires null completed_at and error")
        else:
            if self.completed_at is None or self.completed_at < self.started_at:
                raise ValueError("completed batches require completed_at >= started_at")
            if (self.status == "FAILED") != (self.error is not None):
                raise ValueError("FAILED requires an error; READY requires null error")
        return self


class ManifestFile(StrictRecord):
    kind: FileKind
    path: str
    count: Annotated[int, Field(ge=0, le=INT64_MAX)]
    sha256: SHA256

    @model_validator(mode="after")
    def canonical_relative_path(self) -> Self:
        if self.path != f"{self.kind}.jsonl":
            raise ValueError("path must be the kind's canonical relative JSONL filename")
        return self


class Manifest(StrictRecord):
    schema_version: Literal["1.0"]
    dataset_id: Literal["ringlens_demo_v1"]
    batch_id: BatchId
    mode: ImportMode
    seed: Annotated[int, Field(ge=0, le=INT64_MAX)]
    as_of: Timestamp
    files: list[ManifestFile]

    @model_validator(mode="after")
    def canonical_file_set(self) -> Self:
        if tuple(file.kind for file in self.files) != FILE_KINDS:
            raise ValueError(
                "files must contain accounts, devices, addresses, orders, refunds in order"
            )
        return self
