"""Reproducible RingLens examples on isolated FalkorDB graphs.

Run with ``uv run python example/demo/demo.py --example 04`` from the repository
root. Dry-run validates inputs and manifests without contacting FalkorDB.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from ringlens.agent import get_agent_run, run_agent
from ringlens.agent_provider import AgentSettings, ProviderError, create_provider
from ringlens.cases import apply_case_policy, get_case
from ringlens.config import Settings
from ringlens.dataset_io import DatasetBatch, make_batch, validate_batch
from ringlens.graph_store import GraphStore, GraphStoreError
from ringlens.ingest import ingest_batch
from ringlens.investigator import investigate
from ringlens.models import FILE_KINDS, RefundId, StrictRecord, Timestamp, canonical_json
from ringlens.review_models import ReviewRequest
from ringlens.reviews import compare_evidence, record_review, review_history
from ringlens.schema import REFUND_CONTEXT_QUERY, initialize_schema
from ringlens.validation import SourceRecords
from ringlens.verify import verify_graph

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = {
    f"{index:02}": ROOT / "example" / f"example_{index:02}" / "data.json" for index in range(1, 6)
}


class DemoError(ValueError):
    """Expected validation or read-back failure suitable for display."""


class ExampleBatch(StrictRecord):
    as_of: Timestamp
    records: SourceRecords


class ExampleInput(StrictRecord):
    schema_version: Literal["1.0"]
    seed: Timestamp
    seed_refund_id: RefundId
    base: ExampleBatch
    append: ExampleBatch | None = None


@dataclass(frozen=True)
class PreparedExample:
    number: str
    seed_refund_id: str
    base: DatasetBatch
    append: DatasetBatch | None
    final_records: SourceRecords


@dataclass(frozen=True)
class DemoOutcome:
    graph_name: str
    score: int
    priority: str
    candidate_count: int
    maximum_depth: int
    case_action: str
    case_id: str | None
    refund_count: int
    review_id: str | None


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DemoError("Example JSON has duplicate keys.")
        result[key] = value
    return result


def load_example(number: str) -> PreparedExample:
    """Parse strict source rows and derive the existing canonical batch contract."""
    if number not in EXAMPLES:
        raise DemoError("Choose an example from 01 through 05.")
    path = EXAMPLES[number]
    try:
        payload = path.read_bytes()
    except OSError:
        raise DemoError(f"Cannot read example {number}; check {path}.") from None
    try:
        source = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_keys)
        if payload != (canonical_json(source) + "\n").encode("utf-8"):
            raise DemoError(f"{path.name} is not canonical UTF-8 JSON with one final LF.")
    except DemoError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise DemoError(f"Example {number} must be valid UTF-8 JSON.") from None
    try:
        example = ExampleInput.model_validate(source)
    except ValidationError as error:
        issue = error.errors(include_input=False, include_context=False, include_url=False)[0]
        location = ".".join(str(part) for part in issue["loc"])
        raise DemoError(f"Example {number} field {location}: {issue['msg']}") from None
    try:
        base = make_batch(
            example.base.records, seed=example.seed, as_of=example.base.as_of, mode="base"
        )
        validate_batch(base)
    except ValueError as error:
        raise DemoError(f"Example {number} fails base validation: {error}") from None
    if example.seed_refund_id not in {refund.id for refund in base.records.refunds}:
        raise DemoError("The seed refund must exist in the base batch.")
    append = None
    final_records = base.records
    if example.append is not None:
        append = make_batch(
            example.append.records, seed=example.seed, as_of=example.append.as_of, mode="append"
        )
        if append.manifest.as_of <= base.manifest.as_of:
            raise DemoError("Append as_of must advance beyond the base horizon.")
        try:
            final_records = validate_batch(append, base=base)
        except ValueError as error:
            raise DemoError(f"Example {number} fails append validation: {error}") from None
    return PreparedExample(number, example.seed_refund_id, base, append, final_records)


def _counts(records: SourceRecords) -> str:
    return ", ".join(f"{kind}={len(getattr(records, kind))}" for kind in FILE_KINDS)


def report_dry_run(example: PreparedExample) -> None:
    print(f"[1/4] Example {example.number}: strict records and cross-record checks passed.")
    print(f"[2/4] Base: {_counts(example.base.records)}")
    print(f"      base batch ID: {example.base.manifest.batch_id}")
    if example.append is not None:
        print(f"      append: {_counts(example.append.records)}")
        print(f"      append batch ID: {example.append.manifest.batch_id}")
    print(f"[3/4] Seed refund {example.seed_refund_id} resolves in the base records.")
    print(
        "[4/4] DRY RUN ONLY: no database connection, graph writes, investigation, or case action."
    )


def _verify_import(store: GraphStore, records: SourceRecords, as_of: int) -> int:
    report = verify_graph(store, expected=records)
    if not report.ok or report.dataset_status != "READY" or report.as_of != as_of:
        raise DemoError("FalkorDB graph verification failed: " + "; ".join(report.issues))
    return report.counts["refunds"]


def _verify_seed_path(store: GraphStore, refund_id: str) -> None:
    rows = store.read(REFUND_CONTEXT_QUERY, {"refund_id": refund_id}).result_set
    if len(rows) != 1 or rows[0][1] != refund_id:
        raise DemoError("Seed refund context was not uniquely readable from FalkorDB.")
    print(
        "      FalkorDB read-back: requesting account="
        f"{rows[0][0]}, order={rows[0][2]}, device={rows[0][3]}, address={rows[0][4]}"
    )


def _assess(store: GraphStore, refund_id: str):
    result = investigate(store, refund_id)
    if result.completeness != "COMPLETE" or result.total_score is None or result.priority is None:
        raise DemoError("Investigation was incomplete; no score or case action is asserted.")
    rules = (
        ", ".join(
            f"{finding.evidence.rule_id}+{int(finding.evidence.contribution)}"
            for finding in result.triggered_rules
        )
        or "none"
    )
    print(
        f"      Graph assessment: {len(result.candidates)} accounts, "
        f"score={result.total_score}/{result.priority}, rules={rules}"
    )
    first = sum(candidate.depth <= 1 for candidate in result.candidates)
    print(f"      Discovery: {first} after expansion one; {len(result.candidates)} after two.")
    for candidate in result.candidates:
        if candidate.depth == 2 and candidate.seed_path:
            nodes = candidate.seed_path["nodes"]
            edges = candidate.seed_path["edges"]
            route = nodes[0]
            for left, right in zip(nodes, nodes[1:], strict=False):
                links = [
                    edge
                    for edge in edges
                    if {edge["source_id"], edge["target_id"]} == {left, right}
                ]
                if len(links) != 1:
                    raise DemoError("A bridge path has missing or ambiguous directed edges.")
                edge = links[0]
                if edge["source_id"] == left:
                    route += f" -[{edge['type']}]-> {right}"
                else:
                    route += f" <-[{edge['type']}]- {right}"
            print("      Two-expansion path: " + route)
            break
    return result


def _apply_and_verify_case(store: GraphStore, result):
    action = apply_case_policy(store, result)
    print(
        f"      Case policy: {action.action}"
        + (f", case={action.case_id}" if action.case_id else "")
    )
    if result.priority == "LOW":
        if action.action != "NO_CASE_REQUIRED" or action.case_id is not None:
            raise DemoError("A new LOW group unexpectedly received a case.")
        return action
    if action.action != "CREATED" or not action.case_id:
        raise DemoError("A new qualifying group did not publish a case.")
    saved = get_case(store, action.case_id)
    if (
        saved.investigation.publication_status != "READY"
        or saved.investigation.status != "OPEN"
        or saved.investigation.score != result.total_score
        or saved.investigation.priority != result.priority
        or saved.account_ids != sorted(candidate.id for candidate in result.candidates)
        or saved.investigation.latest_assessment_id != action.assessment_id
    ):
        raise DemoError("Published case read-back disagrees with the assessment.")
    repeated = apply_case_policy(store, result)
    if repeated.action != "EXISTING_UNCHANGED" or repeated.case_id != action.case_id:
        raise DemoError("Repeating case policy did not safely reuse the published case.")
    print("      Case read-back: READY/OPEN, score and membership match; repeat is unchanged.")
    return action


def run_live(
    example: PreparedExample,
    *,
    graph_name: str,
    reviewer_id: str | None = None,
    review_outcome: str | None = None,
    review_explanation: str | None = None,
    agent_provider=None,
    agent_settings: AgentSettings | None = None,
) -> DemoOutcome:
    """Import into a new graph, investigate, apply policy, and read back results.

    The graph is intentionally retained to demonstrate persistence. Callers may
    clean up only their own disposable graph after inspection.
    """
    if not graph_name.startswith("ringlens_example_"):
        raise DemoError("Demo graphs must use the ringlens_example_ prefix.")
    review_fields = (reviewer_id, review_outcome, review_explanation)
    if any(review_fields) and not all(review_fields):
        raise DemoError("Supply reviewer ID, outcome, and explanation together.")
    if reviewer_id and example.append is None:
        raise DemoError("An explicit review in this runner requires an append example (05).")
    settings = Settings(graph_name=graph_name)
    review_id = None
    with GraphStore(settings) as store:
        print("[1/5] Checking FalkorDB and reserving a new disposable graph...")
        store.check_health()
        if store.graph_exists():
            raise DemoError(
                "Graph already exists; the demo will not modify it. Choose a fresh run."
            )
        print(f"      Graph: {graph_name}")
        schema = initialize_schema(store)
        print(f"      Schema: {schema.created_indexes} indexes initialized.")
        print("[2/5] Importing the validated base batch...")
        base_result = ingest_batch(store, example.base)
        if base_result.status != "READY":
            raise DemoError("Base batch was not confirmed READY.")
        count = _verify_import(store, example.base.records, example.base.manifest.as_of)
        print(f"      FalkorDB verified: {_counts(example.base.records)}; batch READY.")
        _verify_seed_path(store, example.seed_refund_id)
        print("[3/5] Following actual FalkorDB paths and scoring the base evidence...")
        base_assessment = _assess(store, example.seed_refund_id)
        if reviewer_id:
            print("      Recording the explicitly supplied human review of base R1/R5 facts...")
            review = record_review(
                store,
                ReviewRequest(
                    refund_id=example.seed_refund_id,
                    reviewer_id=reviewer_id,
                    outcome=review_outcome,
                    explanation=review_explanation,
                    rule_ids=["R1", "R5"],
                ),
            )
            review_id = review.id
            if review_id not in {item.id for item in review_history(store)}:
                raise DemoError("Review write was not readable from FalkorDB.")
            print(f"      Review read-back: {review_id} ({review.outcome}).")
        if example.append is not None:
            print("[4/5] Appending later source events, then reassessing the same refund...")
            imported = ingest_batch(store, example.append)
            if imported.status != "READY":
                raise DemoError("Append batch was not confirmed READY.")
            count = _verify_import(store, example.final_records, example.append.manifest.as_of)
            assessment = _assess(store, example.seed_refund_id)
            if review_id:
                context = compare_evidence(store, assessment)
                if review_id not in {item.id for item in context.reviews}:
                    raise DemoError("Earlier review was not available to the new assessment.")
                print(
                    f"      Memory comparison: {len(context.benign_covered)} benign-covered, "
                    f"{len(context.material_new)} material-new evidence facts."
                )
        else:
            print("[4/5] No append batch for this example.")
            assessment = base_assessment
        print("[5/5] Applying deterministic case policy and verifying persistence...")
        action = _apply_and_verify_case(store, assessment)
        outcome = DemoOutcome(
            graph_name=graph_name,
            score=assessment.total_score,
            priority=assessment.priority,
            candidate_count=len(assessment.candidates),
            maximum_depth=max(candidate.depth for candidate in assessment.candidates),
            case_action=action.action,
            case_id=action.case_id,
            refund_count=count,
            review_id=review_id,
        )
    # A new connection verifies the result survives the runner's first connection.
    with GraphStore(settings) as reopened:
        _verify_import(
            reopened,
            example.final_records,
            (example.append.manifest.as_of if example.append else example.base.manifest.as_of),
        )
        _verify_seed_path(reopened, example.seed_refund_id)
        if outcome.case_id:
            saved = get_case(reopened, outcome.case_id)
            if saved.investigation.score != outcome.score:
                raise DemoError("Case did not match after opening a fresh database connection.")
        if review_id and review_id not in {item.id for item in review_history(reopened)}:
            raise DemoError("Review did not remain readable after reconnecting.")
    print("PASS: graph, source records, and any case/review were read back on a new connection.")
    print(f"Persisted disposable graph: {graph_name}")
    if agent_provider is not None:
        print("Optional paid provider: requesting a read-only tool-calling explanation...")
        with GraphStore(settings) as agent_store:
            agent = run_agent(
                agent_store,
                f"Explain refund {example.seed_refund_id} and its graph evidence.",
                mode="assess",
                provider=agent_provider,
                settings=agent_settings,
                required_refund_id=example.seed_refund_id,
            )
        print(
            f"AgentRun {agent.run_id}: {agent.status}; "
            f"tools={', '.join(item['tool'] for item in agent.activities) or 'none'}"
        )
        print(agent.response)
        with GraphStore(settings) as audit_store:
            audit = get_agent_run(audit_store, agent.run_id)
            if audit.get("status") != agent.status:
                raise DemoError("AgentRun audit was not readable after reconnecting.")
        if agent.status != "COMPLETE":
            raise DemoError("Live provider run was incomplete; no agent success is claimed.")
    return outcome


def _arguments(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--example", choices=sorted(EXAMPLES), default="04")
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate only; never contact FalkorDB"
    )
    parser.add_argument(
        "--with-agent", action="store_true", help="Make an opt-in paid provider call"
    )
    parser.add_argument("--reviewer-id", help="Explicit human reviewer ID for example 05")
    parser.add_argument(
        "--review-outcome",
        choices=("BENIGN_EXPLANATION", "NEEDS_MORE_INFORMATION", "CONFIRMED_ABUSE"),
    )
    parser.add_argument("--review-explanation", help="Human-authored review explanation")
    args = parser.parse_args(argv)
    review_fields = (args.reviewer_id, args.review_outcome, args.review_explanation)
    if any(review_fields) and (not all(review_fields) or args.example != "05"):
        parser.error("Supply all review fields together, and only for example 05.")
    if args.dry_run and args.with_agent:
        parser.error("--with-agent requires a live run; dry-run never calls external services.")
    if args.dry_run and any(review_fields):
        parser.error("Dry-run cannot record a human review.")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    provider = None
    try:
        example = load_example(args.example)
        if args.dry_run:
            report_dry_run(example)
            return 0
        agent_settings = AgentSettings() if args.with_agent else None
        if agent_settings is not None:
            provider = create_provider(agent_settings)
        run_live(
            example,
            graph_name="ringlens_example_" + uuid4().hex,
            reviewer_id=args.reviewer_id,
            review_outcome=args.review_outcome,
            review_explanation=args.review_explanation,
            agent_provider=provider,
            agent_settings=agent_settings,
        )
        return 0
    except (DemoError, GraphStoreError, ProviderError, ValueError) as error:
        print(f"DEMO FAILED: {error}", file=sys.stderr)
        print(
            "No success is claimed. Any named demo graph was left intact for inspection.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "DEMO FAILED: Unexpected error; inspect the disposable graph before retrying.",
            file=sys.stderr,
        )
        return 1
    finally:
        if provider is not None:
            provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
