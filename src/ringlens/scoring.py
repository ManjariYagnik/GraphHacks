"""Pure v1 prioritization arithmetic over graph-returned, validated source records."""

from collections import defaultdict

from ringlens.investigation_models import (
    AccountRate,
    ObservationWindow,
    RefundBurst,
    RuleSignal,
    ScoreAnalysis,
    ScoringConfig,
)
from ringlens.models import FILE_KINDS, Refund
from ringlens.validation import SourceRecords


def priority_for_score(score: int) -> str:
    if type(score) is not int or not 0 <= score <= 100:
        raise ValueError("score must be an integer in 0..100")
    return "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"


def refund_bursts(
    refunds: list[Refund], *, min_accounts: int = 3, interval_seconds: int = 1800
) -> list[RefundBurst]:
    """Earliest qualifying rolling interval, consume its events, then continue.

    Both interval endpoints are inclusive. Consume ALL events through start +
    interval_seconds, so overlapping sliding windows cannot reuse an event.
    UTC date attribution uses the first event's Unix day, including across midnight.
    """
    if min_accounts < 3 or interval_seconds < 1:
        raise ValueError("bursts require at least three accounts and a positive interval")
    unique = {}
    for refund in refunds:
        if refund.id in unique and unique[refund.id] != refund:
            raise ValueError("conflicting duplicate refund ID")
        unique[refund.id] = refund
    events = sorted(unique.values(), key=lambda row: (row.requested_at, row.id))
    bursts = []
    left = right = 0
    while left < len(events):
        right = max(right, left)
        while right < len(events) and (
            events[right].requested_at <= events[left].requested_at + interval_seconds
        ):
            right += 1
        members = events[left:right]
        accounts = sorted({row.account_id for row in members})
        if len(accounts) >= min_accounts:
            bursts.append(
                RefundBurst(
                    start=members[0].requested_at,
                    end=members[-1].requested_at,
                    utc_day=members[0].requested_at // 86400,
                    refund_ids=sorted(row.id for row in members),
                    account_ids=accounts,
                )
            )
            left = right
        else:
            left += 1
    return bursts


def score_records(
    records: SourceRecords,
    *,
    candidate_ids: list[str],
    window: ObservationWindow,
    device_user_counts: dict[str, int],
    config: ScoringConfig | None = None,
) -> ScoreAnalysis:
    """No graph discovery or I/O. Counts use distinct IDs, never path multiplicity.

    device_user_counts MUST be independent whole-graph in-window account counts.
    The investigator validates source references before calling this function.
    """
    config = config or ScoringConfig()
    if window.start > window.end:
        raise ValueError("window start must not exceed end")
    # Repeated identical rows are harmless, but conflicting source content is not.
    unique = {}
    for kind in FILE_KINDS:
        indexed = {}
        for row in getattr(records, kind):
            if row.id in indexed and indexed[row.id] != row:
                raise ValueError("conflicting duplicate source ID")
            indexed[row.id] = row
        unique[kind] = indexed
    candidates = set(candidate_ids)
    orders = {
        id_: row
        for id_, row in unique["orders"].items()
        if row.account_id in candidates and window.start <= row.placed_at <= window.end
    }
    refunds = {
        id_: row
        for id_, row in unique["refunds"].items()
        if row.account_id in candidates and window.start <= row.requested_at <= window.end
    }
    device_accounts = defaultdict(set)
    address_accounts = defaultdict(set)
    for order in orders.values():
        device_accounts[order.device_id].add(order.account_id)
        address_accounts[order.address_id].add(order.account_id)
    for device, accounts in device_accounts.items():
        count = device_user_counts.get(device)
        if type(count) is not int or count < len(accounts):
            raise ValueError("whole-graph device user counts are missing or inconsistent")
    shared_devices = sorted(
        device
        for device, accounts in device_accounts.items()
        if len(accounts) >= config.min_accounts
        and device_user_counts[device] <= config.max_device_users
    )
    shared_addresses = sorted(
        address
        for address, accounts in address_accounts.items()
        if len(accounts) >= config.min_accounts
    )
    bursts = refund_bursts(
        list(refunds.values()),
        min_accounts=config.min_accounts,
        interval_seconds=config.burst_seconds,
    )
    rates = []
    for account in sorted(candidates):
        placed = sorted(row.id for row in orders.values() if row.account_id == account)
        requested = sorted(
            (
                row
                for row in refunds.values()
                if row.account_id == account and row.order_id in orders
            ),
            key=lambda row: row.id,
        )
        refunded = sorted({row.order_id for row in requested})
        rates.append(
            AccountRate(
                account_id=account,
                order_ids=placed,
                refunded_order_ids=refunded,
                refund_ids=[row.id for row in requested],
                numerator=len(refunded),
                denominator=len(placed),
                qualifies=len(placed) >= config.min_orders
                and len(refunded) * config.rate_denominator >= len(placed) * config.rate_numerator,
            )
        )
    elevated = [rate for rate in rates if rate.qualifies]
    burst_ids = {id_ for burst in bursts for id_ in burst.refund_ids}
    burst_sources = sorted(
        {
            source
            for id_ in burst_ids
            for source in (id_, refunds[id_].account_id, refunds[id_].order_id)
        }
    )
    device_orders = [row for row in orders.values() if row.device_id in shared_devices]
    address_orders = [row for row in orders.values() if row.address_id in shared_addresses]
    repeated = len({burst.utc_day for burst in bursts}) >= config.repeated_dates
    burst_explanation = (
        f"{len(bursts)} qualifying rolling refund burst(s). "
        f"The first has {len(bursts[0].account_ids)} accounts over "
        f"{bursts[0].end - bursts[0].start} seconds."
        if bursts
        else f"No rolling refund interval contains {config.min_accounts} accounts "
        f"within {config.burst_seconds} seconds."
    )
    rules = [
        RuleSignal(
            rule_id="R1",
            triggered=bool(shared_devices),
            contribution=config.r1_points if shared_devices else 0,
            explanation=(
                f"{len(shared_devices)} device(s) have at least {config.min_accounts} candidate "
                f"users and no more than {config.max_device_users} users across the graph."
            ),
            source_ids=sorted(
                {s for row in device_orders for s in (row.id, row.account_id, row.device_id)}
            ),
            details={
                "eligible_device_ids": shared_devices,
                "devices": [
                    {
                        "device_id": device,
                        "candidate_account_ids": sorted(accounts),
                        "total_users": device_user_counts[device],
                        "orders": [
                            {"id": row.id, "placed_at": row.placed_at}
                            for row in sorted(orders.values(), key=lambda row: row.id)
                            if row.device_id == device
                        ],
                    }
                    for device, accounts in sorted(device_accounts.items())
                ],
            },
        ),
        RuleSignal(
            rule_id="R2",
            triggered=bool(bursts),
            contribution=config.r2_points if bursts else 0,
            explanation=burst_explanation,
            source_ids=burst_sources,
            details={"bursts": [burst.model_dump(mode="json") for burst in bursts]},
        ),
        RuleSignal(
            rule_id="R3",
            triggered=repeated,
            contribution=config.r3_points if repeated else 0,
            explanation=(
                f"Disjoint qualifying bursts occurred on {len({b.utc_day for b in bursts})} "
                "distinct UTC start dates."
            ),
            source_ids=burst_sources,
            details={
                "bursts": [burst.model_dump(mode="json") for burst in bursts],
                "distinct_utc_days": sorted({burst.utc_day for burst in bursts}),
            },
        ),
        RuleSignal(
            rule_id="R4",
            triggered=len(elevated) >= config.min_accounts,
            contribution=config.r4_points if len(elevated) >= config.min_accounts else 0,
            explanation=(
                f"{len(elevated)} accounts placed at least {config.min_orders} orders and "
                f"refunded at least {config.rate_numerator}/{config.rate_denominator} of them."
            ),
            source_ids=sorted(
                {
                    s
                    for rate in elevated
                    for s in [rate.account_id, *rate.order_ids, *rate.refund_ids]
                }
            ),
            details={"accounts": [rate.model_dump(mode="json") for rate in rates]},
        ),
        RuleSignal(
            rule_id="R5",
            triggered=bool(shared_addresses),
            contribution=config.r5_points if shared_addresses else 0,
            explanation=(
                f"{len(shared_addresses)} address(es) received orders from at least "
                f"{config.min_accounts} candidate accounts."
            ),
            source_ids=sorted(
                {s for row in address_orders for s in (row.id, row.account_id, row.address_id)}
            ),
            details={
                "addresses": [
                    {
                        "address_id": address,
                        "account_ids": sorted(address_accounts[address]),
                        "orders": [
                            {"id": row.id, "placed_at": row.placed_at}
                            for row in sorted(address_orders, key=lambda row: row.id)
                            if row.address_id == address
                        ],
                    }
                    for address in shared_addresses
                ]
            },
        ),
    ]
    mitigating = []
    if not bursts:
        mitigating.append(
            f"No {config.min_accounts}-account synchronized refund burst was observed "
            "in the window."
        )
    if len(elevated) < config.min_accounts:
        mitigating.append(
            f"Only {len(elevated)} candidate accounts meet the configured "
            "elevated refund-rate rule."
        )
    for device, count in sorted(device_user_counts.items()):
        if count > config.max_device_users:
            mitigating.append(
                f"Device {device} had {count} users across the graph; it does not qualify for R1."
            )
    if shared_devices or shared_addresses:
        mitigating.append("Shared devices or shipping addresses can have ordinary explanations.")
    total = sum(rule.contribution for rule in rules)
    return ScoreAnalysis(
        total=total,
        priority=priority_for_score(total),
        rules=rules,
        bursts=bursts,
        rates=rates,
        mitigating_context=mitigating,
    )
