"""Single-writer, idempotent case publication with immutable assessment snapshots."""

import time
from hashlib import sha256

from pydantic import TypeAdapter, ValidationError

from ringlens import case_queries as q
from ringlens.case_models import (
    Assessment,
    CaseAction,
    CaseDetail,
    CaseId,
    CaseList,
    CaseRecord,
    CaseSummary,
)
from ringlens.graph_store import GraphStore, GraphStoreError
from ringlens.investigation_models import InvestigationResult
from ringlens.investigator import assert_current_input, build_evidence_findings
from ringlens.models import Evidence, Review, canonical_json
from ringlens.scoring import score_records
from ringlens.validation import validate_records
from ringlens.verify import stable_path


class CaseServiceError(ValueError):
    """A saved case violates its publication/content contract."""


class CaseUnavailableError(CaseServiceError):
    """Case is absent, legacy/unmanaged, or not yet published."""


def _hash(value) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def group_key(dataset_id: str, account_ids: list[str]) -> str:
    return _hash({"dataset_id": dataset_id, "account_ids": sorted(set(account_ids))})


def assessment_key(result: InvestigationResult) -> str:
    """Scoring configuration supplements the specified revision/window/version key."""
    return _hash(
        {
            "group_key": group_key(result.dataset_id, [c.id for c in result.candidates]),
            "input_revision": result.input_revision,
            "window": result.window.model_dump(),
            "rule_version": result.rule_version,
            "scoring_config": result.config.scoring.model_dump(),
        }
    )


def _snapshot(result):
    snapshot = result.model_dump(mode="json")
    for proof in snapshot["paths"]:
        proof["path"] = stable_path(proof["path"])
    return canonical_json(snapshot)


def _core(result):
    """Seed-neutral analytical identity; retain the first seed's full snapshot separately."""
    return {
        "dataset_id": result.dataset_id,
        "input_revision": result.input_revision,
        "account_ids": sorted(c.id for c in result.candidates),
        "window": result.window.model_dump(),
        "rule_version": result.rule_version,
        "scoring_config": result.config.scoring.model_dump(),
        "analysis": result.analysis.model_dump(mode="json"),
        "evidence": [finding.model_dump(mode="json") for finding in result.triggered_rules],
        "source_records": result.source_records.model_dump(mode="json"),
        "device_user_counts": result.device_user_counts,
        "paths": [
            proof.model_copy(update={"path": stable_path(proof.path)}).model_dump(mode="json")
            for proof in sorted(result.paths, key=lambda proof: proof.id)
            if proof.query_id in {"orders.v1", "refunds.v1"}
        ],
    }


def _validate_result(result):
    result = InvestigationResult.model_validate(result)
    if result.completeness != "COMPLETE":
        raise CaseServiceError("Only COMPLETE assessments can be persisted.")
    ids = [c.id for c in result.candidates]
    if len(ids) != len(set(ids)) or result.seed_account_id not in ids:
        raise CaseServiceError("Candidate IDs must be distinct and include the seed account.")
    if result.window.end != result.assessment_as_of or result.window.start != max(
        0, result.assessment_as_of - result.config.window_days * 86400
    ):
        raise CaseServiceError("Assessment window disagrees with its configuration.")
    validate_records(result.source_records, as_of=result.assessment_as_of)
    analysis = score_records(
        result.source_records,
        candidate_ids=ids,
        window=result.window,
        device_user_counts=result.device_user_counts,
        config=result.config.scoring,
    )
    if result.analysis != analysis or result.triggered_rules != build_evidence_findings(
        result, analysis
    ):
        raise CaseServiceError(
            "Assessment measurements/evidence disagree with deterministic scoring."
        )
    return result


def _assessment(properties):
    return Assessment.model_validate({"completed_at": None, **properties})


def _validate_saved(assessment):
    result = _validate_result(InvestigationResult.model_validate_json(assessment.snapshot_json))
    if (
        assessment.id != "asm_" + assessment_key(result)
        or assessment.group_key != group_key(result.dataset_id, [c.id for c in result.candidates])
        or assessment.analytical_hash != _hash(_core(result))
        or assessment.config_hash != _hash(result.config.scoring.model_dump())
        or assessment.input_revision != result.input_revision
        or assessment.dataset_id != result.dataset_id
        or assessment.rule_version != result.rule_version
        or assessment.window_start != result.window.start
        or assessment.window_end != result.window.end
        or assessment.score != result.total_score
        or assessment.priority != result.priority
    ):
        raise CaseServiceError("Stored assessment identity/content disagrees with its snapshot.")
    return result


def _one_case(store, case_id):
    rows = store.read(q.CASE, {"case_id": case_id}).result_set
    if len(rows) > 1:
        raise CaseServiceError("Duplicate Investigation IDs exist; explicit repair is required.")
    if not rows:
        return None
    return CaseRecord.model_validate(rows[0][0])


def _unique_ids(rows, message):
    ids = [row[0] for row in rows]
    if len(ids) != len(set(ids)):
        raise CaseServiceError(message)
    return ids


def _check_evidence(store, expected):
    ids = [evidence.id for evidence in expected]
    rows = store.read(q.EXISTING_EVIDENCE, {"ids": ids}).result_set
    found = {}
    for (props,) in rows:
        evidence = Evidence.model_validate(props)
        if evidence.id in found:
            raise CaseServiceError("Duplicate Evidence IDs exist.")
        found[evidence.id] = evidence
    if any(found.get(e.id, e) != e for e in expected):
        raise CaseServiceError("Evidence ID already has conflicting immutable content.")


def _check_stage(store, case_id, staged, expected_evidence, account_ids):
    params = {"case_id": case_id, "assessment_id": staged.id}
    rows = store.read(q.EXISTING_ASSESSMENT, params).result_set
    if len(rows) != 1 or _assessment(rows[0][0]) != staged:
        raise CaseServiceError(
            "Staged assessment is missing, duplicated, or has conflicting content."
        )
    members = _unique_ids(
        store.read(q.MEMBERS, params).result_set, "Duplicate INCLUDES relationships."
    )
    if sorted(members) != account_ids:
        raise CaseServiceError("Case membership did not match every expected account.")
    history = store.read(q.HISTORY, params).result_set
    if sum(props["id"] == staged.id for props, _ in history) != 1:
        raise CaseServiceError("Expected exactly one HAS_ASSESSMENT link.")
    evidence_rows = store.read(q.STAGED_EVIDENCE, params).result_set
    actual = [Evidence.model_validate(props) for props, _ in evidence_rows]
    if sorted(actual, key=lambda e: e.id) != sorted(expected_evidence, key=lambda e: e.id):
        raise CaseServiceError("Staged evidence endpoints/content are incomplete or duplicated.")
    overlaps = _unique_ids(
        store.read(q.STAGED_OVERLAPS, params).result_set, "Duplicate OVERLAPS links."
    )
    if not set(staged.overlap_case_ids) <= set(overlaps):
        raise CaseServiceError("Expected overlap links are missing.")


def apply_case_policy(store: GraphStore, result: InvestigationResult) -> CaseAction:
    """Persist a COMPLETE medium/high group, or reassess its existing managed case.

    Uses the import lease too: one source/case writer per graph, without fencing
    or general concurrent mutation guarantees. LOADING snapshots are never cases.
    """
    if result.completeness != "COMPLETE":
        return CaseAction(
            action="NOT_CREATED_INCOMPLETE",
            reason="Assessment is INCOMPLETE; no case writes attempted.",
        )
    try:
        result = _validate_result(result)
    except ValueError:
        return CaseAction(
            action="NOT_CREATED_INCOMPLETE",
            reason="Assessment is invalid; run a fresh investigation.",
        )
    account_ids = sorted(c.id for c in result.candidates)
    group = group_key(result.dataset_id, account_ids)
    case_id, assessment_id = "inv_" + group, "asm_" + assessment_key(result)
    action = "CREATED"
    publish_attempted = False
    try:
        # First stale-input check precedes even the transient lease acquisition.
        assert_current_input(store, result)
        with store.import_lock() as renew:
            assert_current_input(store, result)
            case = _one_case(store, case_id)
            action = "UPDATED" if case and case.publication_status == "READY" else "CREATED"
            params = {
                "case_id": case_id,
                "assessment_id": assessment_id,
                "dataset_id": result.dataset_id,
                "account_ids": account_ids,
            }
            overlaps = [row[0] for row in store.read(q.FIND_OVERLAPS, params).result_set]
            if action == "CREATED" and result.priority == "LOW":
                if overlaps:
                    assert_current_input(store, result)
                    renew()
                    if store.write(q.FLAG_OVERLAPS, {"case_ids": overlaps}).result_set != [
                        [len(overlaps)]
                    ]:
                        raise CaseServiceError("Overlap flags did not match every existing case.")
                return CaseAction(
                    action="NO_CASE_REQUIRED",
                    reason="Complete LOW assessment; no new case required.",
                    requires_review=bool(overlaps),
                    overlapping_case_ids=overlaps,
                )
            if case:
                if case.publication_status == "READY":
                    get_case(store, case_id)  # Reject corrupt history before any update.
                members = _unique_ids(
                    store.read(q.MEMBERS, params).result_set, "Duplicate INCLUDES links."
                )
                if (
                    case.group_key != group
                    or case.dataset_id != result.dataset_id
                    or set(members) - set(account_ids)
                    or case.publication_status == "READY"
                    and sorted(members) != account_ids
                ):
                    raise CaseServiceError(
                        "Existing case group/membership conflicts with the assessment."
                    )
            existing = store.read(q.EXISTING_ASSESSMENT, params).result_set
            if len(existing) > 1:
                raise CaseServiceError("Duplicate Assessment IDs exist.")
            staged = _assessment(existing[0][0]) if existing else None
            if staged:
                _validate_saved(staged)
                if staged.analytical_hash != _hash(_core(result)):
                    raise CaseServiceError(
                        "Assessment key was reused with different analytical content."
                    )
                if staged.status == "READY":
                    saved = get_case(store, case_id)
                    if staged.id not in {a.id for a in saved.assessments}:
                        raise CaseServiceError(
                            "READY assessment is missing its completed case link."
                        )
                    return CaseAction(
                        action="EXISTING_UNCHANGED",
                        case_id=case_id,
                        assessment_id=assessment_id,
                        reason="Identical completed group assessment already exists.",
                        requires_review=saved.investigation.requires_review,
                        overlapping_case_ids=saved.overlapping_case_ids,
                    )
            expected_evidence = [finding.evidence for finding in result.triggered_rules]
            _check_evidence(store, expected_evidence)
            now = max(int(time.time()), case.created_at if case else 0)
            if staged is None:
                staged = Assessment(
                    id=assessment_id,
                    dataset_id=result.dataset_id,
                    group_key=group,
                    input_revision=result.input_revision,
                    window_start=result.window.start,
                    window_end=result.window.end,
                    rule_version=result.rule_version,
                    config_hash=_hash(result.config.scoring.model_dump()),
                    analytical_hash=_hash(_core(result)),
                    score=result.total_score,
                    priority=result.priority,
                    snapshot_json=_snapshot(result),
                    overlap_case_ids=overlaps,
                    status="LOADING",
                    created_at=now,
                    completed_at=None,
                )
            initial = CaseRecord(
                id=case_id,
                dataset_id=result.dataset_id,
                group_key=group,
                publication_status="LOADING",
                status="OPEN",
                priority=result.priority,
                score=result.total_score,
                rule_version=result.rule_version,
                assessment_as_of=result.assessment_as_of,
                created_at=now,
                updated_at=now,
            )

            def write(query, values, count):
                renew()
                if store.write(query, values).result_set != [[count]]:
                    raise CaseServiceError(
                        "Case write did not match every expected endpoint; retry safely."
                    )

            write(
                q.START,
                {
                    **params,
                    "case_properties": initial.model_dump(exclude_none=True),
                    "assessment_properties": staged.model_dump(exclude_none=True),
                },
                1,
            )
            write(q.INCLUDE, params, len(account_ids))
            if expected_evidence:
                write(
                    q.EVIDENCE,
                    {**params, "rows": [e.model_dump() for e in expected_evidence]},
                    len(expected_evidence),
                )
            overlap_ids = sorted(set(overlaps) | set(staged.overlap_case_ids))
            if overlap_ids:
                pairs = [
                    dict(zip(("left", "right"), sorted([case_id, other]), strict=True))
                    for other in overlap_ids
                ]
                write(q.LINK_OVERLAPS, {"pairs": pairs}, len(pairs))
            _check_stage(store, case_id, staged, expected_evidence, account_ids)
            dataset, _ = assert_current_input(store, result)
            publish_attempted = True
            write(
                q.PUBLISH_WITH_EVIDENCE if expected_evidence else q.PUBLISH,
                {
                    **params,
                    "horizon": dataset.as_of,
                    "group_key": group,
                    "analytical_hash": staged.analytical_hash,
                    "has_overlaps": bool(overlap_ids),
                    "completed_at": max(int(time.time()), staged.created_at, now),
                },
                1,
            )
            saved = get_case(store, case_id)
            return CaseAction(
                action=action,
                case_id=case_id,
                assessment_id=assessment_id,
                reason="Assessment published; reviewer status and earlier history preserved.",
                requires_review=saved.investigation.requires_review,
                overlapping_case_ids=saved.overlapping_case_ids,
            )
    except Exception as error:
        # A final acknowledgement can be lost after publication. Reconcile from
        # the authoritative completion marker when connectivity permits.
        try:
            saved = get_case(store, case_id) if publish_attempted else None
            if saved and assessment_id in {a.id for a in saved.assessments}:
                return CaseAction(
                    action=action,
                    case_id=case_id,
                    assessment_id=assessment_id,
                    reason="Publication confirmed from stored completion marker after an error.",
                    requires_review=saved.investigation.requires_review,
                    overlapping_case_ids=saved.overlapping_case_ids,
                )
        except Exception:
            pass
        message = (
            "Stored case data is invalid."
            if isinstance(error, ValidationError)
            else str(error)
            if isinstance(error, (ValueError, GraphStoreError))
            else f"Unexpected {type(error).__name__}; retry the original request."
        )
        return CaseAction(
            action="NOT_CREATED_INCOMPLETE",
            case_id=case_id,
            assessment_id=assessment_id,
            reason=f"Case persistence not completed/acknowledged: {message} "
            "Run a fresh assessment and retry; staged records are not published cases.",
        )


def get_case(store: GraphStore, case_id: str) -> CaseDetail:
    """Read only published history; validate latest-reference and per-assessment evidence."""
    case_id = TypeAdapter(CaseId).validate_python(case_id, strict=True)
    if not store.graph_exists():
        raise CaseUnavailableError("Case graph does not exist.")
    params = {"case_id": case_id}
    case = _one_case(store, case_id)
    if case is None or case.publication_status != "READY":
        raise CaseUnavailableError("Case is absent or not published; retry an interrupted request.")
    accounts = _unique_ids(store.read(q.MEMBERS, params).result_set, "Duplicate INCLUDES links.")
    history = [_assessment(props) for props, _ in store.read(q.HISTORY, params).result_set]
    if len(history) != len({a.id for a in history}):
        raise CaseServiceError("Duplicate assessment IDs or HAS_ASSESSMENT links.")
    ready = [a for a in history if a.status == "READY"]
    snapshots = {a.id: _validate_saved(a) for a in ready}
    for item in ready:
        if item.group_key != case.group_key or item.dataset_id != case.dataset_id:
            raise CaseServiceError("Assessment belongs to a different case group/dataset.")
        if sorted(c.id for c in snapshots[item.id].candidates) != accounts:
            raise CaseServiceError("Case membership differs from immutable assessment membership.")
    latest = store.read(q.LATEST, params).result_set
    if len(latest) != 1 or latest[0][:2] != [case.latest_assessment_id, "READY"]:
        raise CaseServiceError("Case latest-assessment property/edge/completion marker disagree.")
    current = next((a for a in ready if a.id == case.latest_assessment_id), None)
    if current is None or (case.score, case.priority, case.rule_version, case.assessment_as_of) != (
        current.score,
        current.priority,
        current.rule_version,
        current.window_end,
    ):
        raise CaseServiceError(
            "Case current priority/score disagrees with its latest completed assessment."
        )
    evidence = {a.id: [] for a in ready}
    for assessment_id, properties, _ in store.read(q.EVIDENCE_FOR_HISTORY, params).result_set:
        evidence[assessment_id].append(Evidence.model_validate(properties))
    for item in ready:
        expected = [finding.evidence for finding in snapshots[item.id].triggered_rules]
        if sorted(evidence[item.id], key=lambda e: e.id) != sorted(expected, key=lambda e: e.id):
            raise CaseServiceError(
                "Completed assessment evidence is missing, duplicated, or conflicting."
            )
        if sum(e.contribution for e in evidence[item.id]) != item.score:
            raise CaseServiceError("Assessment score differs from its own evidence contributions.")
    compatible = _unique_ids(
        store.read(q.COMPATIBLE_EVIDENCE, params).result_set, "Duplicate case evidence links."
    )
    if set(compatible) != {e.id for items in evidence.values() for e in items}:
        raise CaseServiceError(
            "Compatible case evidence links disagree with published assessment history."
        )
    overlaps = _unique_ids(store.read(q.OVERLAPS, params).result_set, "Duplicate OVERLAPS links.")
    reviews, review_links, review_edges = {}, {}, {}
    for props, parent_link, evidence_id, about_link in store.read(q.REVIEWS, params).result_set:
        review = Review.model_validate(props)
        if review.id in review_links and review_links[review.id] != parent_link:
            raise CaseServiceError("Duplicate HAS_REVIEW links.")
        reviews[review.id], review_links[review.id] = review, parent_link
        if evidence_id is not None:
            review_edges.setdefault(review.id, {})[about_link] = evidence_id
    return CaseDetail(
        investigation=case,
        account_ids=accounts,
        assessments=ready,
        evidence_by_assessment=evidence,
        reviews=[reviews[id_] for id_ in sorted(reviews)],
        review_evidence_ids={
            id_: sorted(set(review_edges.get(id_, {}).values())) for id_ in sorted(reviews)
        },
        overlapping_case_ids=overlaps,
        pending_assessment_ids=[a.id for a in history if a.status != "READY"],
    )


def list_cases(store: GraphStore, *, limit: int = 100) -> CaseList:
    """Presentation cap is disclosed; no LOADING case is shown as completed."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("case list limit must be an integer in 1..1000")
    if not store.graph_exists():
        return CaseList(cases=[], truncated=False, pending_case_count=0)
    rows = store.read(q.LIST, {"limit": limit + 1}).result_set
    summaries = []
    for (case_id,) in rows[:limit]:
        saved = get_case(store, case_id)
        summaries.append(
            CaseSummary(
                investigation=saved.investigation,
                account_ids=saved.account_ids,
                assessment_count=len(saved.assessments),
                pending_assessment_count=len(saved.pending_assessment_ids),
                overlapping_case_ids=saved.overlapping_case_ids,
            )
        )
    pending = store.read(q.PENDING_CASES).result_set[0][0]
    return CaseList(cases=summaries, truncated=len(rows) > limit, pending_case_count=pending)
