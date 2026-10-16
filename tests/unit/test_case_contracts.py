"""Pure identity/publication contracts require neither a generator nor a graph."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from ringlens.case_models import Assessment, CaseRecord
from ringlens.cases import apply_case_policy, assessment_key, group_key, list_cases
from ringlens.investigation_models import (
    CandidateAccount,
    InvestigationConfig,
    InvestigationResult,
    ObservationWindow,
    ScoringConfig,
)


def result():
    return InvestigationResult(
        seed_refund_id="ref_one",
        seed_account_id="acct_one",
        dataset_id="ringlens_demo_v1",
        input_revision="a" * 64,
        assessment_as_of=100000,
        window=ObservationWindow(start=0, end=100000),
        candidates=[
            CandidateAccount(id="acct_one", depth=0),
            CandidateAccount(id="acct_two", depth=1),
        ],
        config=InvestigationConfig(),
    )


def test_keys_ignore_seed_and_order_but_separate_revision_window_and_config():
    first = result()
    other = first.model_copy(
        update={
            "seed_refund_id": "ref_two",
            "seed_account_id": "acct_two",
            "candidates": list(reversed(first.candidates)),
        }
    )
    assert assessment_key(first) == assessment_key(other)
    assert group_key("ringlens_demo_v1", ["acct_two", "acct_one"]) == group_key(
        "ringlens_demo_v1", ["acct_one", "acct_two"]
    )
    assert group_key("other", ["acct_one", "acct_two"]) != group_key(
        "ringlens_demo_v1", ["acct_one", "acct_two"]
    )
    for values in (
        {"input_revision": "b" * 64},
        {"window": ObservationWindow(start=1, end=100000)},
        {"config": InvestigationConfig(scoring=ScoringConfig(r1_points=0))},
    ):
        assert assessment_key(first.model_copy(update=values)) != assessment_key(first)


def test_incomplete_case_policy_performs_no_database_access():
    store = MagicMock()
    assert apply_case_policy(store, result()).action == "NOT_CREATED_INCOMPLETE"
    assert not store.mock_calls


def assessment(**changes):
    return Assessment(
        **{
            "id": "asm_" + "a" * 64,
            "dataset_id": "ringlens_demo_v1",
            "group_key": "b" * 64,
            "input_revision": "c" * 64,
            "window_start": 1,
            "window_end": 100,
            "rule_version": "v1",
            "config_hash": "d" * 64,
            "analytical_hash": "e" * 64,
            "score": 90,
            "priority": "HIGH",
            "snapshot_json": '{"a":1}',
            "overlap_case_ids": [],
            "status": "LOADING",
            "created_at": 100,
            "completed_at": None,
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "READY"},
        {"completed_at": 101},
        {"window_start": 101},
        {"score": True},
        {"score": 101},
        {"created_at": "100"},
        {"snapshot_json": {"a": 1}},
        {"snapshot_json": '{"a":NaN}'},
        {"snapshot_json": '{"a":1,"a":2}'},
        {"unknown": 1},
    ],
)
def test_assessment_publication_and_json_validation(changes):
    with pytest.raises(ValidationError):
        assessment(**changes)


def test_assessment_valid_completion_and_canonical_json():
    assert (
        assessment(status="READY", completed_at=101, snapshot_json='{"b":2, "a":1}').snapshot_json
        == '{"a":1,"b":2}'
    )


def test_case_extends_investigation_without_publishing_partial_history():
    fields = dict(
        id="inv_" + "a" * 64,
        dataset_id="ringlens_demo_v1",
        group_key="a" * 64,
        status="OPEN",
        priority="HIGH",
        score=90,
        rule_version="v1",
        assessment_as_of=100,
        created_at=101,
        updated_at=101,
        publication_status="LOADING",
    )
    assert CaseRecord(**fields).latest_assessment_id is None
    fields["publication_status"] = "READY"
    with pytest.raises(ValidationError, match="latest"):
        CaseRecord(**fields)
    assert CaseRecord(**fields, latest_assessment_id="asm_" + "b" * 64).status == "OPEN"


@pytest.mark.parametrize("limit", [0, 1001, True, "1"])
def test_case_list_bounds_checked_before_database_access(limit):
    store = MagicMock()
    with pytest.raises(ValueError):
        list_cases(store, limit=limit)
    assert not store.mock_calls
