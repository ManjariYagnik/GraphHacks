"""One bounded native tool-calling loop with graph audit and grounded final output."""

import json
import time
from uuid import uuid4

from pydantic import Field

from ringlens.agent_provider import AgentSettings, ProviderError
from ringlens.agent_tools import AgentTools, tool_schemas
from ringlens.cases import assessment_key
from ringlens.models import StrictRecord, canonical_json


class AgentResult(StrictRecord):
    run_id: str
    status: str
    mode: str
    response: str
    references: list[str]
    activities: list[dict]
    errors: list[str] = Field(default_factory=list)


SAVE_RUN = """
MERGE (r:AgentRun {id: $id}) SET r += $properties RETURN count(r)
"""
READ_RUN = "MATCH (r:AgentRun {id: $id}) RETURN properties(r)"


def _grounded(tools):
    lines, refs = [], set()
    for reference, result in tools.assessments.items():
        lines.append(
            f"Refund {result.seed_refund_id}: {result.completeness}; "
            f"score={result.total_score}, priority={result.priority}; "
            f"{len(result.candidates)} candidate accounts."
        )
        refs.add(result.seed_refund_id)
        refs.update(c.id for c in result.candidates)
        for c in result.candidates[:20]:
            refs.add(c.id)
            lines.append(f"Account {c.id}, discovery depth {c.depth}.")
            if c.depth:
                lines.append("Graph connection: " + " -> ".join(c.seed_path["nodes"]) + ".")
        if len(result.candidates) > 20:
            lines.append(f"Display omitted {len(result.candidates) - 20} candidate paths.")
        for finding in result.triggered_rules:
            refs.add(finding.evidence.id)
            refs.update(finding.evidence.source_ids)
            lines.append(
                f"{finding.evidence.rule_id} +{finding.evidence.contribution:g}: "
                f"{finding.explanation} Evidence {finding.evidence.id}."
            )
        context = tools.contexts.get(reference)
        if context:
            for review in context.reviews[:20]:
                refs.add(review.id)
                reviewed = json.loads(review.scope_json)["facts"]
                refs.update(f["evidence_id"] for f in reviewed)
                lines.append(
                    f"Prior human review {review.id} by {review.reviewer_id}: {review.outcome}; "
                    f"{len(review.fingerprints)} scoped facts considered."
                )
                lines.append(
                    "Reviewed scope: "
                    + ", ".join(f"{f['rule_id']} evidence {f['evidence_id']}" for f in reviewed)
                    + "."
                )
            if len(context.reviews) > 20:
                lines.append(f"Display omitted {len(context.reviews) - 20} prior reviews.")
            lines.append(
                f"Reviewed/benign-covered facts: {len(context.benign_covered)}; "
                f"unexplained: {len(context.unexplained)}; "
                f"material new facts: {len(context.material_new)}."
            )
            for fact in context.facts:
                if fact.fingerprint in context.material_new:
                    lines.append(
                        f"New/unexplained {fact.rule_id}: evidence {fact.evidence_id}; "
                        f"sources {', '.join(fact.source_ids[:12])}."
                    )
        elif reference in tools.analyzed_references:
            lines.append("Review context was not retrieved; memory comparison is incomplete.")
        else:
            lines.append("Historical snapshot; fresh analysis is required for current disposition.")
        action = tools.actions.get(reference)
        if action:
            lines.append(
                f"Case action: {action.action}; case {action.case_id or 'none'}. {action.reason}"
            )
            if action.case_id:
                refs.add(action.case_id)
            if action.assessment_id:
                refs.add(action.assessment_id)
        else:
            lines.append("No case write was confirmed in this run.")
        lines.extend(f"Limitation: {message}" for message in result.limitations)
        lines.extend(f"Mitigating context: {message}" for message in result.mitigating_context)
        lines.extend(f"Analysis error: {issue.message}" for issue in result.errors)
    for id_, case in tools.cases.items():
        refs.add(id_)
        lines.append(
            f"Stored case {id_}: {case.investigation.status}; "
            f"{case.investigation.priority} score={case.investigation.score:g}; "
            f"{len(case.assessments)} completed assessments."
        )
        for review in case.reviews:
            refs.add(review.id)
            lines.append(f"Human review {review.id}: {review.outcome}.")
        for review in tools.case_reviews.get(id_, []):
            refs.add(review.id)
            lines.append(
                f"Scoped history {review.id} by {review.reviewer_id}: {review.outcome}; "
                f"assessment {review.assessment_id}; supersedes {review.supersedes or 'none'}."
            )
    lines.append("Priority is a demonstration heuristic, not proof of wrongdoing.")
    lines.extend(f"Tool error: {message}" for message in tools.failures)
    return "\n".join(lines)[:16000], sorted(refs)


def run_agent(
    store, message: str, *, mode="assess", provider, settings: AgentSettings | None = None
):
    if not isinstance(message, str) or not message.strip() or len(message) > 4000:
        raise ValueError("Agent message must contain 1..4000 characters.")
    settings = settings or AgentSettings()
    tools = AgentTools(store, mode=mode)
    id_, started = "run_" + uuid4().hex, int(time.time())
    errors, status = [], "RUNNING"

    def audit(final=""):
        props = {
            "mode": mode,
            "status": status,
            "started_at": started,
            "requested_refund_ids": sorted(tools.requested_refunds),
            "requested_case_ids": sorted(tools.requested_cases),
            "assessment_references": list(tools.assessments),
            "assessment_ids": sorted(
                {
                    "asm_" + assessment_key(result)
                    for result in tools.assessments.values()
                    if result.window
                }
            ),
            "case_ids": sorted(
                {a.case_id for a in tools.actions.values() if a.case_id} | set(tools.cases)
            ),
            "activity_json": canonical_json({"activities": tools.activities}),
            "final_response": final,
            "errors_json": canonical_json({"errors": errors}),
        }
        if status != "RUNNING":
            props["completed_at"] = max(started, int(time.time()))
        if store.write(SAVE_RUN, {"id": id_, "properties": props}).result_set != [[1]]:
            raise ValueError("Agent run audit was not acknowledged.")

    audit()
    history = [{"role": "user", "content": message}]
    cached_calls = {}
    deadline = time.monotonic() + settings.agent_timeout_seconds * settings.agent_max_turns * 2
    try:
        for _ in range(settings.agent_max_turns):
            if time.monotonic() > deadline:
                raise ProviderError("Agent run deadline exceeded.")
            turn = provider.next_turn(history, tool_schemas(), mode)
            history.extend(turn.items)  # Reasoning stays in memory only, never in audit.
            for call in turn.calls:
                if len(tools.activities) >= settings.agent_max_tool_calls:
                    raise ProviderError("Agent tool-call limit reached.")
                if len(call.get("arguments", "")) > 8000:
                    raise ProviderError("Provider tool arguments exceeded their input bound.")
                signature = (call["name"], call["arguments"])
                if call["call_id"] in cached_calls:
                    old_signature, output = cached_calls[call["call_id"]]
                    if signature != old_signature:
                        raise ProviderError("Provider reused a call ID with changed arguments.")
                else:
                    output = tools.execute(call["name"], call["arguments"])
                    cached_calls[call["call_id"]] = (signature, output)
                history.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": canonical_json(output),
                    }
                )
                audit()
            if turn.finished:
                complete = bool(tools.assessments or tools.cases)
                for reference, result in tools.assessments.items():
                    if reference not in tools.analyzed_references:
                        continue  # Historical evidence lookup is not a new assessment request.
                    complete &= result.completeness == "COMPLETE" and reference in tools.contexts
                    if mode == "investigate":
                        complete &= (
                            reference in tools.actions
                            and tools.actions[reference].action != "NOT_CREATED_INCOMPLETE"
                        )
                status = "COMPLETE" if complete else "INCOMPLETE"
                if not complete:
                    errors.append(
                        "Required complete analysis, review context, or authorized case action "
                        "is missing."
                    )
                break
        else:
            status = "INCOMPLETE"
            errors.append("Agent tool-loop turn limit reached.")
    except Exception as error:
        status = "FAILED"
        errors.append(
            str(error)
            if isinstance(error, ProviderError)
            else "Agent execution/audit failed; inspect confirmed tool actions before retrying."
        )
    response, refs = _grounded(tools)
    failures = [item for item in tools.activities if item["status"] != "OK"]
    if failures:
        if status == "COMPLETE":
            status = "INCOMPLETE"
        errors.append(
            f"{len(failures)} tool calls returned errors/incomplete results; "
            "no unconfirmed write is asserted."
        )
    response += "\n" + "\n".join(errors)
    try:
        audit(response)
    except Exception:
        status = "INCOMPLETE"
        errors.append(
            "Final run audit could not be confirmed; graph case actions may already be saved."
        )
    return AgentResult(
        run_id=id_,
        status=status,
        mode=mode,
        response=response,
        references=refs,
        activities=tools.activities,
        errors=errors,
    )


def get_agent_run(store, run_id):
    if not isinstance(run_id, str) or not run_id.startswith("run_") or len(run_id) != 36:
        raise ValueError("Invalid AgentRun ID.")
    rows = store.read(READ_RUN, {"id": run_id}).result_set
    if len(rows) != 1:
        raise ValueError("AgentRun is missing or ambiguous.")
    return rows[0][0]
