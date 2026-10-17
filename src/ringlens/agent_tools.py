"""Validated tool boundary. Only server-issued handles or saved snapshots are accepted."""

from typing import Annotated
from uuid import uuid4

from pydantic import Field, ValidationError

from ringlens import cases
from ringlens.investigation_models import InvestigationConfig
from ringlens.investigator import investigate
from ringlens.models import AccountId, RefundId, StrictRecord, canonical_json
from ringlens.reviews import compare_evidence, review_history
from ringlens.synthetic import parse_as_of
from ringlens.verify import stable_path


class AnalyzeArguments(StrictRecord):
    refund_id: RefundId
    as_of: str | None = None
    window_days: Annotated[int, Field(ge=1, le=3650)] | None = None


class ReferenceArguments(StrictRecord):
    assessment_reference: Annotated[str, Field(min_length=1, max_length=100)]


class EvidenceArguments(ReferenceArguments):
    account_id: AccountId | None = None
    rule_id: Annotated[str, Field(pattern=r"^R[1-5]$")] | None = None


class CaseArguments(StrictRecord):
    case_id: cases.CaseId


ARGUMENTS = {
    "analyze_refund": AnalyzeArguments,
    "get_evidence": EvidenceArguments,
    "get_review_context": ReferenceArguments,
    "apply_case_policy": ReferenceArguments,
    "get_case": CaseArguments,
}
DESCRIPTIONS = {
    "analyze_refund": (
        "Read-only graph discovery and deterministic priority. "
        "Returns an application assessment_reference."
    ),
    "get_evidence": "Get actual paths/measurements; optionally filter account or rule.",
    "get_review_context": "Read scoped human decisions and material changes before case action.",
    "apply_case_policy": (
        "Write-enabled only. Recheck human memory/current data, "
        "then apply deterministic create/update/reopen/skip policy."
    ),
    "get_case": "Read published case, immutable history and human reviews. No writes.",
}


def tool_schemas():
    def strict_schema(schema):
        if isinstance(schema, dict):
            schema.pop("default", None)
            if schema.get("type") == "object":
                schema["additionalProperties"] = False
                schema["required"] = list(schema.get("properties", {}))
            for value in schema.values():
                strict_schema(value)
        elif isinstance(schema, list):
            for value in schema:
                strict_schema(value)

    result = []
    for name, model in ARGUMENTS.items():
        schema = model.model_json_schema()
        strict_schema(schema)
        result.append(
            {
                "type": "function",
                "name": name,
                "description": DESCRIPTIONS[name],
                "parameters": schema,
                "strict": True,
            }
        )
    return result


class AgentTools:
    def __init__(self, store, *, mode="assess"):
        if mode not in {"assess", "investigate"}:
            raise ValueError("Invalid agent mode.")
        self.store, self.mode = store, mode
        self.assessments, self.contexts, self.actions, self.cases = {}, {}, {}, {}
        self.requested_refunds, self.requested_cases = set(), set()
        self.activities = []
        self.failures = []
        self.case_reviews = {}
        self.analyzed_references = set()

    def resolve(self, reference):
        if reference in self.assessments:
            return self.assessments[reference]
        if reference.startswith("asm_") and len(reference) == 68:
            from ringlens import case_queries as q

            rows = self.store.read(q.EXISTING_ASSESSMENT, {"assessment_id": reference}).result_set
            if len(rows) == 1:
                saved = cases._assessment(rows[0][0])
                if saved.status == "READY":
                    result = cases._validate_saved(saved)
                    self.assessments[reference] = result
                    return result
        raise ValueError(
            "Assessment reference is unknown/expired or not a READY snapshot; analyze again."
        )

    def execute(self, name, arguments):
        """Explicit errors, bounded results. No score/assessment payload in tool inputs."""
        try:
            if name not in ARGUMENTS:
                raise ValueError("Unknown tool; human review mutation is never an agent tool.")
            params = (
                ARGUMENTS[name].model_validate_json(arguments)
                if isinstance(arguments, str)
                else ARGUMENTS[name].model_validate(arguments)
            )
            if name == "apply_case_policy" and self.mode != "investigate":
                raise PermissionError("Assess mode cannot create, update, or reopen cases.")
            data = self._execute(name, params)
            output = {"status": "OK", "tool": name, "data": data, "errors": []}
            if name == "analyze_refund" and data["completeness"] != "COMPLETE":
                output["status"] = "INCOMPLETE"
            if name == "apply_case_policy" and data["action"] == "NOT_CREATED_INCOMPLETE":
                output["status"] = "INCOMPLETE"
            if len(canonical_json(output).encode()) > 60000:
                raise ValueError(
                    "Tool result exceeds display budget; request a narrower evidence filter."
                )
        except Exception as error:
            from ringlens.graph_store import GraphStoreError

            message = (
                "Invalid typed tool arguments."
                if isinstance(error, ValidationError)
                else str(error)
                if isinstance(error, (ValueError, PermissionError, GraphStoreError))
                else "Tool failed unexpectedly; no action success is asserted."
            )
            output = {"status": "ERROR", "tool": name, "data": {}, "errors": [message]}
            self.failures.append(message)
        self.activities.append(
            {
                "tool": name,
                "status": output["status"],
                "case_id": output["data"].get("case_id"),
                "assessment_reference": output["data"].get("assessment_reference"),
                "action": output["data"].get("action"),
            }
        )
        if output["errors"]:
            self.activities[-1]["error"] = output["errors"][0][:500]
        return output

    def _execute(self, name, params):
        if name == "analyze_refund":
            self.requested_refunds.add(params.refund_id)
            result = investigate(
                self.store,
                params.refund_id,
                as_of=parse_as_of(params.as_of) if params.as_of else None,
                config=InvestigationConfig(window_days=params.window_days or 30),
            )
            ref = "ar_" + uuid4().hex
            self.assessments[ref] = result
            self.analyzed_references.add(ref)
            return {
                "assessment_reference": ref,
                "refund_id": result.seed_refund_id,
                "assessment_id": "asm_" + cases.assessment_key(result) if result.window else None,
                "assessment_storage": "Unsaved unless a review or case has published this key.",
                "completeness": result.completeness,
                "score": result.total_score,
                "priority": result.priority,
                "as_of": result.assessment_as_of,
                "candidate_accounts": [c.model_dump(mode="json") for c in result.candidates],
                "rules": [
                    {
                        "id": s.rule_id,
                        "triggered": s.triggered,
                        "points": s.points,
                        "contribution": s.contribution,
                        "thresholds": s.thresholds,
                    }
                    for s in result.analysis.rules
                ]
                if result.analysis
                else [],
                "evidence_ids": [f.evidence.id for f in result.triggered_rules],
                "summary": result.explanation,
                "limitations": result.limitations,
                "errors": [e.model_dump() for e in result.errors],
            }
        if name == "get_case":
            self.requested_cases.add(params.case_id)
            saved = cases.get_case(self.store, params.case_id)
            self.cases[params.case_id] = saved
            history = [
                r
                for r in review_history(self.store, dataset_id=saved.investigation.dataset_id)
                if r.case_id == params.case_id or set(r.account_ids).intersection(saved.account_ids)
            ]
            self.case_reviews[params.case_id] = history
            evidence_ids = sorted(
                {e.id for values in saved.evidence_by_assessment.values() for e in values}
            )
            return {
                "case_id": params.case_id,
                "case": saved.investigation.model_dump(mode="json"),
                "assessments": [
                    {"id": a.id, "score": a.score, "priority": a.priority, "as_of": a.window_end}
                    for a in saved.assessments[-20:]
                ],
                "omitted_assessments": max(0, len(saved.assessments) - 20),
                "reviews": [r.model_dump(mode="json") for r in saved.reviews[-20:]],
                "omitted_reviews": max(0, len(saved.reviews) - 20),
                "scoped_reviews": [
                    {
                        "id": r.id,
                        "reviewer_id": r.reviewer_id,
                        "outcome": r.outcome,
                        "explanation": r.explanation[:1000],
                        "assessment_id": r.assessment_id,
                        "supersedes": r.supersedes,
                        "reviewed_at": r.reviewed_at,
                        "evidence_ids": r.evidence_ids,
                        "rule_version": r.rule_version,
                        "valid_until": r.valid_until,
                    }
                    for r in history[-20:]
                ],
                "omitted_scoped_reviews": max(0, len(history) - 20),
                "evidence_ids": evidence_ids[:100],
                "omitted_evidence_ids": max(0, len(evidence_ids) - 100),
            }
        reference = params.assessment_reference
        result = self.resolve(reference)
        if result.completeness != "COMPLETE":
            raise ValueError(
                "Assessment is INCOMPLETE; no final priority/case action is available."
            )
        if name == "get_review_context":
            context = compare_evidence(self.store, result)
            self.contexts[reference] = context
            return {
                "assessment_reference": reference,
                "reviews": [
                    {
                        "id": r.id,
                        "reviewer_id": r.reviewer_id,
                        "outcome": r.outcome,
                        "explanation": r.explanation[:1000],
                        "assessment_id": r.assessment_id,
                        "fingerprints": r.fingerprints,
                    }
                    for r in context.reviews[:20]
                ],
                "omitted_reviews": max(0, len(context.reviews) - 20),
                "considered": context.considered,
                "benign_covered": context.benign_covered,
                "unexplained": context.unexplained,
                "material_new": context.material_new,
                "facts": [
                    {
                        "fingerprint": f.fingerprint,
                        "rule_id": f.rule_id,
                        "evidence_id": f.evidence_id,
                        "source_ids": f.source_ids,
                    }
                    for f in context.facts
                ],
                "recommendation": context.recommendation,
                "case_id": context.case_id,
                "limitations": context.limitations,
            }
        if name == "apply_case_policy":
            if reference not in self.contexts:
                raise ValueError(
                    "Consult get_review_context for this assessment before a case action."
                )
            action = cases.apply_case_policy(self.store, result)
            self.actions[reference] = action
            return action.model_dump(mode="json")
        findings = [
            f
            for f in result.triggered_rules
            if not params.rule_id or f.evidence.rule_id == params.rule_id
        ]
        ids = {p for finding in findings for p in finding.path_ids}
        paths = [
            p
            for p in result.paths
            if (not params.rule_id or p.id in ids)
            and (not params.account_id or params.account_id in p.source_ids)
        ]
        return {
            "assessment_reference": reference,
            "evidence": [f.model_dump(mode="json") for f in findings],
            "paths": [
                {"id": p.id, "query_id": p.query_id, "path": stable_path(p.path)}
                for p in paths[:20]
            ],
            "omitted_paths": max(0, len(paths) - 20),
            "limitations": result.limitations,
        }
