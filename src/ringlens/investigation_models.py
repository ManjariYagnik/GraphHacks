"""Read-only investigation contracts; these objects are never graph case nodes."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from ringlens.models import (
    SHA256,
    AccountId,
    Evidence,
    OrderId,
    RefundId,
    SourceId,
    StrictRecord,
    Timestamp,
)
from ringlens.validation import SourceRecords


class ScoringConfig(StrictRecord):
    rule_version: Literal["v1"] = "v1"
    min_accounts: Annotated[int, Field(ge=3)] = 3
    max_device_users: Annotated[int, Field(ge=3)] = 5
    burst_seconds: Annotated[int, Field(ge=1)] = 1800
    repeated_dates: Annotated[int, Field(ge=2)] = 2
    min_orders: Annotated[int, Field(ge=1)] = 5
    rate_numerator: Annotated[int, Field(ge=1)] = 1
    rate_denominator: Annotated[int, Field(ge=1)] = 2
    r1_points: Annotated[int, Field(ge=0, le=100)] = 25
    r2_points: Annotated[int, Field(ge=0, le=100)] = 25
    r3_points: Annotated[int, Field(ge=0, le=100)] = 25
    r4_points: Annotated[int, Field(ge=0, le=100)] = 15
    r5_points: Annotated[int, Field(ge=0, le=100)] = 10

    @model_validator(mode="after")
    def valid_rules(self) -> Self:
        if self.rate_numerator > self.rate_denominator:
            raise ValueError("refund-rate threshold cannot exceed one")
        if sum(getattr(self, f"r{i}_points") for i in range(1, 6)) > 100:
            raise ValueError("rule contributions must total at most 100")
        return self


class InvestigationConfig(StrictRecord):
    window_days: Annotated[int, Field(ge=1, le=3650)] = 30
    max_accounts: Annotated[int, Field(ge=1, le=1000)] = 100
    max_evidence: Annotated[int, Field(ge=1, le=100_000)] = 2000
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)


class ObservationWindow(StrictRecord):
    start: Timestamp
    end: Timestamp
    boundaries: Literal["inclusive"] = "inclusive"

    @model_validator(mode="after")
    def ordered_window(self) -> Self:
        if self.start > self.end:
            raise ValueError("window start must not exceed end")
        return self


class PathProof(StrictRecord):
    id: str
    query_id: str
    window: ObservationWindow
    source_ids: list[SourceId]
    path: dict[str, JsonValue]


class CandidateAccount(StrictRecord):
    id: AccountId
    depth: Literal[0, 1, 2]
    reached_from: AccountId | None = None
    path_ids: list[str] = Field(default_factory=list)
    seed_path: dict[str, JsonValue] | None = None


class RefundBurst(StrictRecord):
    start: Timestamp
    end: Timestamp
    utc_day: int
    refund_ids: list[RefundId]
    account_ids: list[AccountId]


class AccountRate(StrictRecord):
    account_id: AccountId
    order_ids: list[OrderId]
    refunded_order_ids: list[OrderId]
    refund_ids: list[RefundId]
    numerator: int
    denominator: int
    qualifies: bool


class RuleSignal(StrictRecord):
    rule_id: Literal["R1", "R2", "R3", "R4", "R5"]
    triggered: bool
    contribution: Annotated[int, Field(ge=0, le=100)]
    points: Annotated[int, Field(ge=0, le=100)]
    thresholds: dict[str, JsonValue]
    explanation: str
    source_ids: list[SourceId]
    details: dict[str, JsonValue]


class ScoreAnalysis(StrictRecord):
    total: Annotated[int, Field(ge=0, le=100)]
    priority: Literal["LOW", "MEDIUM", "HIGH"]
    rules: list[RuleSignal]
    bursts: list[RefundBurst]
    rates: list[AccountRate]
    mitigating_context: list[str]


class EvidenceFinding(StrictRecord):
    evidence: Evidence
    explanation: str
    path_ids: list[str]
    query_ids: list[str]


class InvestigationIssue(StrictRecord):
    code: str
    message: str
    query_id: str | None = None


class InvestigationResult(StrictRecord):
    seed_refund_id: RefundId
    seed_account_id: AccountId | None = None
    assessment_as_of: Timestamp | None = None
    window: ObservationWindow | None = None
    dataset_id: str | None = None
    input_revision: SHA256 | None = None
    successful_batch_ids: list[str] = Field(default_factory=list)
    rule_version: Literal["v1"] = "v1"
    config: InvestigationConfig
    completeness: Literal["COMPLETE", "INCOMPLETE"] = "INCOMPLETE"
    candidates: list[CandidateAccount] = Field(default_factory=list)
    triggered_rules: list[EvidenceFinding] = Field(default_factory=list)
    analysis: ScoreAnalysis | None = None
    total_score: Annotated[int, Field(ge=0, le=100)] | None = None
    priority: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    paths: list[PathProof] = Field(default_factory=list)
    source_records: SourceRecords = Field(default_factory=SourceRecords)
    device_user_counts: dict[str, int] = Field(default_factory=dict)
    query_ids: list[str] = Field(default_factory=list)
    explanation: str = "Investigation is incomplete; no final priority is available."
    mitigating_context: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    errors: list[InvestigationIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def final_score_requires_complete(self) -> Self:
        if self.completeness == "INCOMPLETE" and (
            self.total_score is not None or self.priority is not None or self.analysis is not None
        ):
            raise ValueError("incomplete investigations cannot have a final score or priority")
        if self.completeness == "COMPLETE" and (
            self.total_score is None or self.priority is None or self.analysis is None
        ):
            raise ValueError("complete investigations require an analysis, score, and priority")
        if self.completeness == "COMPLETE" and (
            self.errors
            or self.analysis.total != self.total_score
            or self.analysis.priority != self.priority
            or self.window is None
            or self.seed_account_id is None
            or self.input_revision is None
        ):
            raise ValueError("complete investigations require consistent analysis and provenance")
        return self
