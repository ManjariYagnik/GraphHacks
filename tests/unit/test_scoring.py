"""Independent small rule fixtures: no generator, graph, or evaluation labels."""

import pytest
from pydantic import ValidationError

from ringlens.investigation_models import (
    InvestigationConfig,
    InvestigationResult,
    ObservationWindow,
    ScoringConfig,
)
from ringlens.models import Account, Address, Device, Order, Refund
from ringlens.scoring import priority_for_score, refund_bursts, score_records
from ringlens.validation import SourceRecords


def records(*, refunds_per_account=3, shared_address=True):
    accounts = [Account(id=f"acct_{i}", created_at=0) for i in range(3)]
    addresses = [Address(id=f"addr_{i}", label=f"Synthetic address {i}") for i in range(3)]
    orders, refunds = [], []
    for i, account in enumerate(accounts):
        for j in range(5):
            order = Order(
                id=f"ord_{i}_{j}",
                account_id=account.id,
                device_id="dev_0",
                address_id=f"addr_{0 if shared_address else i}",
                placed_at=100 + j,
                amount_minor=10000 + i * 37 + j,
                currency="INR",
            )
            orders.append(order)
            if j < refunds_per_account:
                refunds.append(
                    Refund(
                        id=f"ref_{i}_{j}",
                        account_id=account.id,
                        order_id=order.id,
                        requested_at=86400 * (j + 1) + i * 600,
                        amount_minor=1000,
                        currency="INR",
                    )
                )
    return SourceRecords(
        accounts=accounts,
        devices=[Device(id="dev_0")],
        addresses=addresses,
        orders=orders,
        refunds=refunds,
    )


def analyze(rows=None, *, counts=None, config=None, window=None):
    rows = rows or records()
    return score_records(
        rows,
        candidate_ids=[a.id for a in rows.accounts],
        window=window or ObservationWindow(start=0, end=400000),
        device_user_counts=counts if counts is not None else {"dev_0": 3},
        config=config,
    )


def test_all_rules_once_and_independent_counts():
    result = analyze()
    assert result.total == 100 and result.priority == "HIGH"
    assert [rule.contribution for rule in result.rules] == [25, 25, 25, 15, 10]
    assert len(result.bursts) == 3
    assert [len(burst.account_ids) for burst in result.bursts] == [3, 3, 3]
    assert all((rate.numerator, rate.denominator) == (3, 5) for rate in result.rates)


def test_shared_infrastructure_alone_remains_low():
    rows = records(refunds_per_account=1)
    rows = rows.model_copy(
        update={
            "refunds": [
                refund.model_copy(update={"requested_at": (i + 1) * 86400})
                for i, refund in enumerate(rows.refunds)
            ]
        }
    )
    result = analyze(rows)
    assert result.total == 35 and result.priority == "LOW"
    assert [r.rule_id for r in result.rules if r.triggered] == ["R1", "R5"]
    assert result.bursts == [] and result.mitigating_context


@pytest.mark.parametrize(
    ("score", "priority"),
    [(0, "LOW"), (39, "LOW"), (40, "MEDIUM"), (69, "MEDIUM"), (70, "HIGH"), (100, "HIGH")],
)
def test_priority_boundaries(score, priority):
    assert priority_for_score(score) == priority


@pytest.mark.parametrize("score", [-1, 101, True, "40", 40.0])
def test_invalid_score(score):
    with pytest.raises(ValueError):
        priority_for_score(score)


def events(times, accounts=None):
    accounts = accounts or list(range(len(times)))
    return [
        Refund(
            id=f"ref_{i}",
            account_id=f"acct_{account}",
            order_id=f"ord_{i}",
            requested_at=time,
            amount_minor=1,
            currency="INR",
        )
        for i, (time, account) in enumerate(zip(times, accounts, strict=True))
    ]


def test_rolling_interval_crosses_fixed_clock_buckets_inclusively():
    # 00:20, 00:40, 00:50: a real 30-minute interval spans clock buckets.
    bursts = refund_bursts(events([1200, 2400, 3000]))
    assert len(bursts) == 1 and bursts[0].end - bursts[0].start == 1800
    assert refund_bursts(events([1200, 2400, 3001])) == []


def test_overlapping_windows_do_not_reuse_events_even_across_midnight():
    times = [86000, 86300, 86500, 86700, 86900, 87200]
    bursts = refund_bursts(events(times, [0, 1, 2, 0, 1, 2]))
    assert len(bursts) == 1
    assert bursts[0].utc_day == 0 and len(bursts[0].refund_ids) == 6


def test_disjoint_same_date_bursts_do_not_trigger_repetition():
    rows = records()
    original = sorted(
        (row for row in rows.refunds if row.order_id.endswith(("_0", "_1"))),
        key=lambda row: row.requested_at,
    )
    refunds = [
        row.model_copy(update={"requested_at": time})
        for row, time in zip(original, [1000, 1100, 1200, 10000, 10100, 10200], strict=True)
    ]
    result = analyze(rows.model_copy(update={"refunds": refunds}))
    assert len(result.bursts) == 2 and result.rules[1].triggered
    assert not result.rules[2].triggered


def test_repetition_requires_disjoint_events_on_distinct_start_dates():
    result = refund_bursts(events([100, 200, 300, 86500, 86600, 86700], [0, 1, 2, 0, 1, 2]))
    assert len(result) == 2 and len({burst.utc_day for burst in result}) == 2
    assert not set(result[0].refund_ids).intersection(result[1].refund_ids)


def test_multiple_refunds_from_one_account_do_not_make_a_burst():
    assert refund_bursts(events([100, 200, 300, 400], [0, 0, 1, 1])) == []


def test_duplicate_source_rows_and_input_order_do_not_change_analysis():
    rows = records()
    duplicate = SourceRecords(
        **{kind: list(reversed(getattr(rows, kind))) * 2 for kind in SourceRecords.model_fields}
    )
    assert analyze(duplicate) == analyze(rows)
    with pytest.raises(ValueError, match="conflicting"):
        analyze(
            rows.model_copy(
                update={
                    "refunds": [
                        *rows.refunds,
                        rows.refunds[0].model_copy(update={"amount_minor": 2}),
                    ]
                }
            )
        )


def test_device_users_are_whole_graph_counts_including_outside_candidates():
    result = analyze(counts={"dev_0": 6})
    assert result.rules[0].details["devices"][0]["candidate_account_ids"] == [
        "acct_0",
        "acct_1",
        "acct_2",
    ]
    assert not result.rules[0].triggered and result.total == 75
    assert analyze(counts={"dev_0": 5}).rules[0].triggered
    for counts in ({}, {"dev_0": 2}, {"dev_0": True}):
        with pytest.raises(ValueError, match="device user"):
            analyze(counts=counts)


def test_window_boundaries_filter_every_signal_and_rate_denominator():
    rows = records()
    # Out-of-window orders with in-window refunds can contribute to bursts, never rates.
    result = analyze(rows, window=ObservationWindow(start=86400, end=174000), counts={})
    assert not result.rules[0].triggered and not result.rules[4].triggered
    assert result.rules[1].triggered and result.rules[2].triggered
    assert all(rate.denominator == rate.numerator == 0 for rate in result.rates)
    assert not result.rules[3].triggered
    assert sorted(id_ for burst in result.bursts for id_ in burst.refund_ids) == sorted(
        refund.id for refund in rows.refunds if 86400 <= refund.requested_at <= 174000
    )


def test_exactly_half_distinct_orders_and_minimum_orders():
    rows = records()
    # Five orders, two refunds is below 50%; six orders, three is exactly 50%.
    assert not analyze(records(refunds_per_account=2)).rules[3].triggered
    more = [
        order.model_copy(update={"id": f"ord_{i}_5"})
        for i, order in enumerate([order for order in rows.orders if order.id.endswith("_0")])
    ]
    assert analyze(rows.model_copy(update={"orders": [*rows.orders, *more]})).rules[3].triggered
    assert not analyze(config=ScoringConfig(min_orders=6)).rules[3].triggered


def test_configuration_is_explicit_and_recorded_arithmetic():
    config = ScoringConfig(r1_points=0, r2_points=10, r3_points=10, r4_points=10, r5_points=10)
    result = analyze(config=config)
    assert result.total == 40 and result.priority == "MEDIUM"
    assert analyze(config=ScoringConfig(min_accounts=4)).total == 0


@pytest.mark.parametrize(
    "values",
    [
        {"r1_points": 26},
        {"rate_numerator": 3},
        {"min_accounts": True},
        {"burst_seconds": "1800"},
        {"rule_version": "v2"},
        {"unexpected": 1},
    ],
)
def test_invalid_rule_configuration(values):
    with pytest.raises(ValidationError):
        ScoringConfig(**values)


def test_incomplete_cannot_be_low_or_have_a_final_score():
    for fields in ({"priority": "LOW"}, {"total_score": 0}, {"analysis": analyze()}):
        with pytest.raises(ValidationError, match="incomplete"):
            InvestigationResult(seed_refund_id="ref_a", config=InvestigationConfig(), **fields)
