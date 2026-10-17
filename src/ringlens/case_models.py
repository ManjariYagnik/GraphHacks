"""Additive case-publication contracts; canonical source and review models stay unchanged."""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from ringlens.models import (
    SHA256,
    AccountId,
    Evidence,
    Investigation,
    NonemptyString,
    Review,
    StrictRecord,
    Timestamp,
)

type CaseId = Annotated[str, Field(pattern=r"^inv_[A-Za-z0-9][A-Za-z0-9_-]*$")]
type AssessmentId = Annotated[str, Field(pattern=r"^asm_[0-9a-f]{64}$")]


class CaseRecord(Investigation):
    dataset_id: Literal["ringlens_demo_v1"]
    group_key: SHA256
    publication_status: Literal["LOADING", "READY"]
    latest_assessment_id: AssessmentId | None = None
    requires_review: bool = False

    @model_validator(mode="after")
    def valid_publication(self) -> Self:
        if self.id != f"inv_{self.group_key}":
            raise ValueError("case ID must derive from group_key")
        if self.publication_status == "READY" and self.latest_assessment_id is None:
            raise ValueError("published cases require a latest assessment")
        return self


class Assessment(StrictRecord):
    id: AssessmentId
    dataset_id: Literal["ringlens_demo_v1"]
    group_key: SHA256
    input_revision: SHA256
    window_start: Timestamp
    window_end: Timestamp
    rule_version: NonemptyString
    config_hash: SHA256
    analytical_hash: SHA256
    score: Annotated[int, Field(ge=0, le=100)]
    priority: Literal["LOW", "MEDIUM", "HIGH"]
    snapshot_json: str
    overlap_case_ids: list[CaseId]
    status: Literal["LOADING", "READY"]
    created_at: Timestamp
    completed_at: Timestamp | None

    @field_validator("snapshot_json")
    @classmethod
    def validated_snapshot_json(cls, value: str) -> str:
        return Evidence.validated_json_string(value)

    @model_validator(mode="after")
    def valid_completion(self) -> Self:
        if self.window_start > self.window_end:
            raise ValueError("assessment window must be ordered")
        if self.status == "LOADING" and self.completed_at is not None:
            raise ValueError("LOADING assessments have no completion time")
        if self.status == "READY" and (
            self.completed_at is None or self.completed_at < self.created_at
        ):
            raise ValueError("READY assessments require completed_at >= created_at")
        if len(self.overlap_case_ids) != len(set(self.overlap_case_ids)):
            raise ValueError("overlapping case IDs must be distinct")
        return self


class CaseAction(StrictRecord):
    action: Literal[
        "CREATED",
        "UPDATED",
        "REOPENED",
        "ALREADY_REVIEWED",
        "EXISTING_UNCHANGED",
        "NO_CASE_REQUIRED",
        "NOT_CREATED_INCOMPLETE",
    ]
    case_id: CaseId | None = None
    assessment_id: AssessmentId | None = None
    reason: str
    requires_review: bool = False
    overlapping_case_ids: list[CaseId] = Field(default_factory=list)


class CaseDetail(StrictRecord):
    investigation: CaseRecord
    account_ids: list[AccountId]
    assessments: list[Assessment]
    evidence_by_assessment: dict[str, list[Evidence]]
    reviews: list[Review]
    review_evidence_ids: dict[str, list[str]]
    overlapping_case_ids: list[CaseId]
    pending_assessment_ids: list[AssessmentId]


class CaseSummary(StrictRecord):
    investigation: CaseRecord
    account_ids: list[AccountId]
    assessment_count: int
    pending_assessment_count: int
    overlapping_case_ids: list[CaseId]


class CaseList(StrictRecord):
    cases: list[CaseSummary]
    truncated: bool
    pending_case_count: int
