"""Human memory scoped to immutable facts, never permanent account clearances."""

import json
import time
from hashlib import sha256

from ringlens import case_queries as cq
from ringlens import review_queries as q
from ringlens.case_models import Assessment
from ringlens.investigation_models import InvestigationConfig, InvestigationResult
from ringlens.investigator import assert_current_input, investigate
from ringlens.models import FILE_KINDS, canonical_json
from ringlens.review_models import EvidenceFact, ReviewContext, ReviewRequest, ScopedReview


def _hash(value):
    return sha256(canonical_json(value).encode()).hexdigest()


def evidence_facts(result: InvestigationResult) -> list[EvidenceFact]:
    """Fingerprint relevant immutable records/measurements; omit cutoff/revision/points."""
    if result.completeness != "COMPLETE":
        raise ValueError("Review comparison requires a COMPLETE assessment.")
    records = {
        row.id: row.model_dump()
        for kind in FILE_KINDS
        for row in getattr(result.source_records, kind)
    }
    facts = []
    for finding in result.triggered_rules:
        rule = finding.evidence.rule_id
        signal = next(s for s in result.analysis.rules if s.rule_id == rule)
        selections = []
        if rule == "R1":
            for device in signal.details["devices"]:
                if device["device_id"] in signal.details["eligible_device_ids"]:
                    ids = [
                        device["device_id"],
                        *device["candidate_account_ids"],
                        *(o["id"] for o in device["orders"]),
                    ]
                    selections.append((device["device_id"], ids, device))
        elif rule in {"R2", "R3"}:
            bursts = signal.details["bursts"]
            groups = [[burst] for burst in bursts] if rule == "R2" else [bursts]
            for group in groups:
                ids = set()
                for burst in group:
                    for rid in burst["refund_ids"]:
                        ids.update((rid, records[rid]["order_id"], records[rid]["account_id"]))
                selections.append(
                    (
                        "refund_burst" if rule == "R2" else "repeated_bursts",
                        sorted(ids),
                        {"bursts": group},
                    )
                )
        elif rule == "R4":
            for account in signal.details["accounts"]:
                if account["qualifies"]:
                    selections.append(
                        (
                            account["account_id"],
                            [account["account_id"], *account["order_ids"], *account["refund_ids"]],
                            account,
                        )
                    )
        else:
            for address in signal.details["addresses"]:
                selections.append(
                    (
                        address["address_id"],
                        [
                            address["address_id"],
                            *address["account_ids"],
                            *(o["id"] for o in address["orders"]),
                        ],
                        address,
                    )
                )
        for anchor, ids, measurement in selections:
            payload = {
                "anchor": anchor,
                "records": [records[id_] for id_ in sorted(set(ids))],
                "measurements": measurement,
            }
            identity = {"rule_id": rule, "rule_version": result.rule_version, "facts": payload}
            facts.append(
                EvidenceFact(
                    fingerprint=_hash(identity),
                    rule_id=rule,
                    rule_version=result.rule_version,
                    evidence_id=finding.evidence.id,
                    source_ids=sorted(set(ids)),
                    facts_json=canonical_json(payload),
                )
            )
    return sorted(facts, key=lambda f: (f.rule_id, f.fingerprint))


def review_history(store, *, dataset_id="ringlens_demo_v1", limit=1000):
    if not store.graph_exists():
        return []
    rows = store.read(q.REVIEWS, {"dataset_id": dataset_id, "limit": limit + 1}).result_set
    if len(rows) > limit:
        raise ValueError("Review-history bound exceeded; comparison is incomplete.")
    return [ScopedReview.model_validate(props) for (props,) in rows]


def _scope_covers(old: EvidenceFact, new: EvidenceFact):
    if (old.rule_id, old.rule_version) != (new.rule_id, new.rule_version):
        return False
    before, after = json.loads(old.facts_json), json.loads(new.facts_json)
    if new.rule_id == "R1" and (
        after["measurements"]["total_users"] > before["measurements"]["total_users"]
    ):
        return False
    return before["anchor"] == after["anchor"] and (
        {_hash(record) for record in after["records"]}
        <= {_hash(record) for record in before["records"]}
    )


def compare_evidence(store, result: InvestigationResult) -> ReviewContext:
    from ringlens import cases

    assert_current_input(store, result)
    facts = evidence_facts(result)
    accounts = {c.id for c in result.candidates}
    history = review_history(store, dataset_id=result.dataset_id)
    superseded = {r.supersedes for r in history if r.supersedes}
    active = [
        r
        for r in history
        if r.id not in superseded
        and r.rule_version == result.rule_version
        and (r.valid_until is None or result.assessment_as_of <= r.valid_until)
        and accounts.intersection(r.account_ids)
    ]
    considered, covered, known_records, prior_facts = set(), set(), set(), []
    for review in active:
        scope = json.loads(review.scope_json)
        previous = [EvidenceFact.model_validate(f) for f in scope["facts"]]
        prior_facts.extend(previous)
        for current in facts:
            if any(_scope_covers(old, current) for old in previous):
                considered.add(current.fingerprint)
                if review.outcome == "BENIGN_EXPLANATION":
                    covered.add(current.fingerprint)
        saved = store.read(
            cq.EXISTING_ASSESSMENT, {"assessment_id": review.assessment_id}
        ).result_set
        if len(saved) != 1:
            raise ValueError("Reviewed assessment snapshot is missing or ambiguous.")
        assessment = cases._assessment(saved[0][0])
        if assessment.status != "READY":
            raise ValueError("Reviewed assessment is not READY.")
        snapshot = cases._validate_saved(assessment)
        canonical_facts = {f.fingerprint: f for f in evidence_facts(snapshot)}
        if any(canonical_facts.get(f.fingerprint) != f for f in previous):
            raise ValueError("Review facts differ from their canonical assessment snapshot.")
        known_records.update(
            _hash(row.model_dump())
            for kind in FILE_KINDS
            for row in getattr(snapshot.source_records, kind)
        )
    case_id = "inv_" + cases.group_key(result.dataset_id, sorted(accounts))
    case = cases._one_case(store, case_id)
    if case and case.publication_status == "READY":
        saved = cases.get_case(store, case_id)
        latest = next(a for a in saved.assessments if a.id == case.latest_assessment_id)
        snapshot = cases._validate_saved(latest)
        if not active:
            prior_facts.extend(evidence_facts(snapshot))
            known_records.update(
                _hash(row.model_dump())
                for kind in FILE_KINDS
                for row in getattr(snapshot.source_records, kind)
            )
    else:
        case = None
    unexplained = sorted(f.fingerprint for f in facts if f.fingerprint not in covered)
    material = sorted(
        f.fingerprint
        for f in facts
        if f.fingerprint in unexplained
        and (
            any(_hash(row) not in known_records for row in json.loads(f.facts_json)["records"])
            or (
                f.rule_id == "R1"
                and any(
                    old.rule_id == "R1"
                    and json.loads(old.facts_json)["anchor"] == json.loads(f.facts_json)["anchor"]
                    and json.loads(f.facts_json)["measurements"]["total_users"]
                    > json.loads(old.facts_json)["measurements"]["total_users"]
                    for old in prior_facts
                )
            )
        )
    )
    recommendation = (
        "ALREADY_REVIEWED"
        if facts and not unexplained
        else "NO_CASE_REQUIRED"
        if result.priority == "LOW"
        else "REOPEN"
        if case and case.status == "CLOSED" and material
        else "KEEP_CLOSED"
        if case and case.status == "CLOSED"
        else "ESCALATE"
    )
    assert_current_input(store, result)
    return ReviewContext(
        reviews=active,
        facts=facts,
        considered=sorted(considered),
        benign_covered=sorted(covered),
        unexplained=unexplained,
        material_new=material,
        recommendation=recommendation,
        case_id=case.id if case else None,
        limitations=[
            "Reviews explain only their immutable fact scope; numeric scores are unchanged.",
            "Superseded or expired reviews do not provide clearance.",
        ],
    )


def persist_assessment(store, result):
    """Caller owns writer lease. Save a reviewed LOW snapshot without creating a case."""
    from ringlens import cases

    result = cases._validate_result(result)
    dataset, _ = assert_current_input(store, result)
    id_ = "asm_" + cases.assessment_key(result)
    rows = store.read(cq.EXISTING_ASSESSMENT, {"assessment_id": id_}).result_set
    if len(rows) > 1:
        raise ValueError("Duplicate assessment IDs.")
    if rows:
        staged = cases._assessment(rows[0][0])
        cases._validate_saved(staged)
        if staged.analytical_hash != cases._hash(cases._core(result)):
            raise ValueError("Immutable reviewed-assessment conflict.")
        if staged.status == "READY":
            return staged
    else:
        staged = Assessment(
            id=id_,
            dataset_id=result.dataset_id,
            group_key=cases.group_key(result.dataset_id, [c.id for c in result.candidates]),
            input_revision=result.input_revision,
            window_start=result.window.start,
            window_end=result.window.end,
            rule_version=result.rule_version,
            config_hash=cases._hash(result.config.scoring.model_dump()),
            analytical_hash=cases._hash(cases._core(result)),
            score=result.total_score,
            priority=result.priority,
            snapshot_json=cases._snapshot(result),
            overlap_case_ids=[],
            status="LOADING",
            created_at=int(time.time()),
            completed_at=None,
        )
    evidence = [f.evidence for f in result.triggered_rules]
    cases._check_evidence(store, evidence)
    if store.write(
        q.START_ASSESSMENT, {"id": id_, "properties": staged.model_dump(exclude_none=True)}
    ).result_set != [[1]]:
        raise ValueError("Assessment staging endpoint mismatch.")
    if evidence and store.write(
        cq.EVIDENCE, {"assessment_id": id_, "rows": [e.model_dump() for e in evidence]}
    ).result_set != [[len(evidence)]]:
        raise ValueError("Assessment evidence endpoint mismatch.")
    actual = store.read(cq.STAGED_EVIDENCE, {"assessment_id": id_}).result_set
    from ringlens.models import Evidence

    if sorted((Evidence.model_validate(p) for p, _ in actual), key=lambda e: e.id) != sorted(
        evidence, key=lambda e: e.id
    ):
        raise ValueError("Reviewed assessment evidence was not fully staged.")
    dataset, _ = assert_current_input(store, result)
    if store.write(
        q.FINISH_ASSESSMENT,
        {
            "id": id_,
            "dataset_id": dataset.id,
            "horizon": dataset.as_of,
            "completed_at": max(int(time.time()), staged.created_at),
        },
    ).result_set != [[1]]:
        raise ValueError("Reviewed assessment publication did not complete.")
    return cases._assessment(
        store.read(cq.EXISTING_ASSESSMENT, {"assessment_id": id_}).result_set[0][0]
    )


def record_review(store, request: ReviewRequest):
    """Explicit human-only write; recompute from graph, append immutable scope and links."""
    from ringlens import cases

    request = ReviewRequest.model_validate(request)
    result = investigate(
        store,
        request.refund_id,
        as_of=request.as_of,
        config=InvestigationConfig(window_days=request.window_days),
    )
    if result.completeness != "COMPLETE":
        raise ValueError("Review requires a complete canonical assessment.")
    facts = [
        f
        for f in evidence_facts(result)
        if (not request.rule_ids or f.rule_id in request.rule_ids)
        and (not request.source_ids or set(request.source_ids).intersection(f.source_ids))
    ]
    if not facts:
        raise ValueError("No evidence matches the explicit review scope.")
    if set(request.rule_ids) - {f.rule_id for f in facts} or set(request.source_ids) - {
        source for f in facts for source in f.source_ids
    }:
        raise ValueError("Every requested rule/source reference must match scoped evidence.")
    if request.valid_until is not None and request.valid_until < result.assessment_as_of:
        raise ValueError("Review validity limit precedes its assessment.")
    with store.import_lock() as renew:
        assert_current_input(store, result)
        if request.case_id:
            case = cases.get_case(store, request.case_id)
            if case.account_ids != sorted(c.id for c in result.candidates):
                raise ValueError("Review case association must match the assessment group.")
        scope = {"facts": [f.model_dump(mode="json") for f in facts]}
        identity = {
            **request.model_dump(mode="json"),
            "assessment_id": "asm_" + cases.assessment_key(result),
            "fingerprints": sorted(f.fingerprint for f in facts),
        }
        id_ = "rev_" + _hash(identity)
        old = None
        if request.supersedes:
            rows = store.read(q.REVIEW, {"id": request.supersedes}).result_set
            if len(rows) != 1:
                raise ValueError("Superseded review is unknown or ambiguous.")
            old = ScopedReview.model_validate(rows[0][0])
            if old.publication_status != "READY":
                raise ValueError("Only a published review can be superseded.")
            superseders = store.read(q.SUPERSEDERS, {"id": old.id}).result_set
            if superseders and superseders != [[id_]]:
                raise ValueError("Review already superseded; correct its latest replacement.")
            if old.dataset_id != result.dataset_id or set(old.fingerprints) != {
                f.fingerprint for f in facts
            }:
                raise ValueError("A correction must retain the original exact fact scope.")
        existing = store.read(q.REVIEW, {"id": id_}).result_set
        if len(existing) > 1:
            raise ValueError("Duplicate review ID.")
        if existing and existing[0][0].get("publication_status") == "READY":
            return ScopedReview.model_validate(existing[0][0])
        assessment = persist_assessment(store, result)
        review = ScopedReview(
            id=id_,
            reviewer_id=request.reviewer_id,
            outcome=request.outcome,
            explanation=request.explanation,
            reviewed_at=max(int(time.time()), old.reviewed_at if old else 0),
            dataset_id=result.dataset_id,
            assessment_id=assessment.id,
            rule_version=result.rule_version,
            account_ids=sorted(c.id for c in result.candidates),
            evidence_ids=sorted({f.evidence_id for f in facts}),
            fingerprints=sorted(f.fingerprint for f in facts),
            scope_json=canonical_json(scope),
            case_id=request.case_id,
            case_status=request.case_status,
            supersedes=request.supersedes,
            valid_until=request.valid_until,
            publication_status="LOADING",
        )
        if existing:
            review = ScopedReview.model_validate(existing[0][0])
        params = {
            "id": id_,
            "assessment_id": assessment.id,
            "properties": review.model_dump(exclude_none=True),
            "evidence_ids": review.evidence_ids,
            "case_id": review.case_id,
            "supersedes": review.supersedes,
        }

        def write(query, count):
            renew()
            if store.write(query, params).result_set != [[count]]:
                raise ValueError(
                    "Review write did not match every endpoint; retry the original request."
                )

        write(q.START_REVIEW, 1)
        write(q.LINK_EVIDENCE, len(review.evidence_ids))
        if review.supersedes:
            write(q.LINK_SUPERSEDES, 1)
        if review.case_id:
            write(q.LINK_CASE, 1)
        links = store.read(q.REVIEW_LINKS, params).result_set
        expected = [
            ("REVIEWS_ASSESSMENT", assessment.id),
            *[("ABOUT", e) for e in review.evidence_ids],
        ]
        if review.supersedes:
            expected.append(("SUPERSEDES", review.supersedes))
        if sorted(map(tuple, links)) != sorted(expected):
            raise ValueError("Review scope links are incomplete or duplicated.")
        dataset, _ = assert_current_input(store, result)
        params.update(
            dataset_id=dataset.id,
            horizon=dataset.as_of,
            case_status=review.case_status,
            recorded_at=review.reviewed_at,
        )
        write(q.FINISH_REVIEW_CASE if review.case_status else q.FINISH_REVIEW, 1)
        return ScopedReview.model_validate(store.read(q.REVIEW, {"id": id_}).result_set[0][0])
