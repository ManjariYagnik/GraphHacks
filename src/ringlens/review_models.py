"""Append-only human review scope and deterministic memory-comparison contracts."""

import json
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from ringlens.case_models import AssessmentId, CaseId
from ringlens.models import (
    SHA256,
    AccountId,
    Evidence,
    RefundId,
    Review,
    SourceId,
    StrictRecord,
    Timestamp,
    canonical_json,
)


class EvidenceFact(StrictRecord):
    fingerprint: SHA256
    rule_id: str
    rule_version: str
    evidence_id: str
    source_ids: list[SourceId]
    facts_json: str

    @field_validator("facts_json")
    @classmethod
    def validated_facts(cls, value):
        return Evidence.validated_json_string(value)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        facts = json.loads(self.facts_json)
        if (
            set(facts) != {"anchor", "records", "measurements"}
            or not isinstance(facts["anchor"], str)
            or not isinstance(facts["records"], list)
            or not isinstance(facts["measurements"], dict)
            or any(
                not isinstance(r, dict) or not isinstance(r.get("id"), str)
                for r in facts["records"]
            )
        ):
            raise ValueError("Invalid evidence fact structure")
        expected = sha256(
            canonical_json(
                {
                    "rule_id": self.rule_id,
                    "rule_version": self.rule_version,
                    "facts": facts,
                }
            ).encode()
        ).hexdigest()
        if self.fingerprint != expected:
            raise ValueError("Evidence fact fingerprint does not match its content")
        if sorted(set(self.source_ids)) != sorted(r["id"] for r in facts["records"]):
            raise ValueError("Fact source IDs do not match records")
        return self


class ScopedReview(Review):
    reviewer_id: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    dataset_id: Literal["ringlens_demo_v1"]
    assessment_id: AssessmentId
    rule_version: str
    account_ids: list[AccountId]
    evidence_ids: list[str]
    fingerprints: list[SHA256]
    scope_json: str
    case_id: CaseId | None = None
    case_status: Literal["OPEN", "IN_REVIEW", "CLOSED", "REOPENED"] | None = None
    supersedes: str | None = None
    valid_until: Timestamp | None = None
    publication_status: Literal["LOADING", "READY"]

    @field_validator("scope_json")
    @classmethod
    def validated_scope(cls, value):
        return Evidence.validated_json_string(value)

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        scope = json.loads(self.scope_json)
        facts = [EvidenceFact.model_validate(f) for f in scope.get("facts", [])]
        if (
            not facts
            or sorted(f.fingerprint for f in facts) != sorted(self.fingerprints)
            or len(self.fingerprints) != len(set(self.fingerprints))
            or sorted({f.evidence_id for f in facts}) != sorted(self.evidence_ids)
            or any(f.rule_version != self.rule_version for f in facts)
        ):
            raise ValueError("Review scope and evidence references do not match")
        return self


class ReviewRequest(StrictRecord):
    refund_id: RefundId
    reviewer_id: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    outcome: Literal["BENIGN_EXPLANATION", "NEEDS_MORE_INFORMATION", "CONFIRMED_ABUSE"]
    explanation: Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")]
    as_of: Timestamp | None = None
    window_days: Annotated[int, Field(ge=1, le=3650)] = 30
    rule_ids: list[Literal["R1", "R2", "R3", "R4", "R5"]] = Field(default_factory=list)
    source_ids: list[SourceId] = Field(default_factory=list)
    case_id: CaseId | None = None
    case_status: Literal["OPEN", "IN_REVIEW", "CLOSED", "REOPENED"] | None = None
    supersedes: str | None = None
    valid_until: Timestamp | None = None

    @model_validator(mode="after")
    def explicit_status(self) -> Self:
        if self.case_status and not self.case_id:
            raise ValueError("case status actions require an explicit case ID")
        return self


class ReviewContext(StrictRecord):
    reviews: list[ScopedReview]
    facts: list[EvidenceFact]
    considered: list[SHA256]
    benign_covered: list[SHA256]
    unexplained: list[SHA256]
    material_new: list[SHA256]
    recommendation: Literal[
        "ALREADY_REVIEWED", "NO_CASE_REQUIRED", "ESCALATE", "REOPEN", "KEEP_CLOSED"
    ]
    case_id: str | None = None
    limitations: list[str] = Field(default_factory=list)
