"""Deterministic synthetic fixtures. Scenario metadata is written only to evaluation/."""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

from pydantic import TypeAdapter

from ringlens.dataset_io import (
    BATCH_FILENAMES,
    DatasetBatch,
    DatasetIOError,
    batch_bytes,
    make_batch,
    validate_batch,
    write_outputs,
)
from ringlens.models import (
    INT64_MAX,
    Account,
    Address,
    Device,
    Order,
    Refund,
    Timestamp,
    canonical_json,
)
from ringlens.validation import SourceRecords

DAY = 86400
WINDOW_SECONDS = 30 * DAY


def parse_as_of(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() != timedelta(0) or parsed.microsecond:
            raise ValueError
        return TypeAdapter(Timestamp).validate_python(int(parsed.timestamp()))
    except (ValueError, OverflowError, OSError):
        raise ValueError(
            "as_of must be a whole-second UTC timestamp, e.g. 2026-10-15T10:00:00Z"
        ) from None


@dataclass(frozen=True)
class SyntheticDemo:
    base: DatasetBatch
    new_evidence: DatasetBatch
    ground_truth: dict[str, object]


def generate_demo(*, seed: int, as_of: int) -> SyntheticDemo:
    """Build records with local seeded randomness and no database or clock access."""
    if type(seed) is not int or not 0 <= seed <= INT64_MAX:
        raise ValueError("seed must be an integer in 0..2^63-1")
    as_of = TypeAdapter(Timestamp).validate_python(as_of)
    if not 120 * DAY <= as_of <= INT64_MAX - 3 * DAY:
        raise ValueError("as_of must leave room for account history and two future evidence days")
    rng = random.Random(seed)
    midnight = as_of - as_of % DAY

    def opaque(prefix: str, ordinal: int) -> str:
        token = sha256(f"ringlens-fixtures-v1:{seed}:{prefix}:{ordinal}".encode()).hexdigest()[:20]
        return f"{prefix}_{token}"

    # Shuffle memberships BEFORE assigning opaque account IDs. No role enters an ID.
    slots = [
        (group, member)
        for group, size in (("ordinary", 38), ("pattern_a", 4), ("pattern_b", 5), ("household", 3))
        for member in range(size)
    ]
    rng.shuffle(slots)
    members = {group: {} for group in ("ordinary", "pattern_a", "pattern_b", "household")}
    for ordinal, (group, member) in enumerate(slots):
        members[group][member] = opaque("acct", ordinal)

    device_ids = [opaque("dev", i) for i in range(42)]
    address_ids = [opaque("addr", i) for i in range(46)]
    rng.shuffle(device_ids)
    rng.shuffle(address_ids)
    assigned_devices = {}
    assigned_addresses = {}
    for member, account_id in members["ordinary"].items():
        assigned_devices[account_id] = [device_ids[member]] * 5
        # Two ordinary pairs share an address; no three-account address cluster.
        address_index = member // 2 if member < 4 else member - 2
        assigned_addresses[account_id] = address_ids[address_index]
    for member, account_id in members["pattern_a"].items():
        assigned_devices[account_id] = [device_ids[38]] * 5
        assigned_addresses[account_id] = address_ids[36 + member]
    for member, account_id in members["pattern_b"].items():
        devices = [device_ids[39]] * 5 if member < 2 else [device_ids[40]] * 5
        if member == 2:
            devices = [
                device_ids[39],
                device_ids[40],
                device_ids[39],
                device_ids[40],
                device_ids[39],
            ]
        assigned_devices[account_id] = devices
        assigned_addresses[account_id] = address_ids[40 + member]
    for account_id in members["household"].values():
        assigned_devices[account_id] = [device_ids[41]] * 5
        assigned_addresses[account_id] = address_ids[45]

    accounts = []
    orders = []
    account_orders = {}
    for ordinal, (group, member) in enumerate(slots):
        account_id = members[group][member]
        owned = []
        for position, day_offset in enumerate((-21, -17, -13, -9, -5)):
            placed_at = midnight + day_offset * DAY + rng.randint(3600, 20 * 3600)
            order = Order(
                id=opaque("ord", ordinal * 5 + position),
                account_id=account_id,
                device_id=assigned_devices[account_id][position],
                address_id=assigned_addresses[account_id],
                placed_at=placed_at,
                amount_minor=rng.randint(15000, 350000),
                currency="INR",
            )
            owned.append(order)
            orders.append(order)
        accounts.append(
            Account(id=account_id, created_at=owned[0].placed_at - rng.randint(2, 75) * DAY)
        )
        account_orders[account_id] = owned

    base_refunds = []
    extra_refunds = []
    refund_ids = {group: [] for group in members}
    next_refund = 0

    def refund(order: Order, requested_at: int) -> Refund:
        nonlocal next_refund
        result = Refund(
            id=opaque("ref", next_refund),
            account_id=order.account_id,
            order_id=order.id,
            requested_at=requested_at,
            amount_minor=rng.randint(max(1, order.amount_minor // 3), order.amount_minor),
            currency="INR",
        )
        next_refund += 1
        return result

    ordinary_selected = rng.sample(range(38), 12)
    ordinary_days = (-19, -18, -16, -15, -14, -12, -10, -8, -6, -4, -3, -1)
    for member, day_offset in zip(ordinary_selected, ordinary_days, strict=True):
        account_id = members["ordinary"][member]
        requested_at = midnight + day_offset * DAY + rng.randint(3600, 22 * 3600)
        eligible = [order for order in account_orders[account_id] if order.placed_at < requested_at]
        record = refund(rng.choice(eligible), requested_at)
        base_refunds.append(record)
        refund_ids["ordinary"].append(record.id)
    for group in ("pattern_a", "pattern_b"):
        for burst, day_offset in enumerate((-11, -7, -2)):
            start = midnight + day_offset * DAY + 12 * 3600 + rng.randint(0, 3600)
            for member in sorted(members[group]):
                account_id = members[group][member]
                record = refund(account_orders[account_id][burst], start + rng.randint(0, 1200))
                base_refunds.append(record)
                refund_ids[group].append(record.id)
    for member, day_offset in enumerate((-20, -16, -8)):
        account_id = members["household"][member]
        record = refund(account_orders[account_id][0], midnight + day_offset * DAY + 12 * 3600)
        base_refunds.append(record)
        refund_ids["household"].append(record.id)

    # Exact 24/48-hour offsets; cap jitter so even near midnight each burst stays
    # on its own UTC date. Simultaneous events are valid when little day remains.
    available_jitter = min(1200, DAY - 1 - as_of % DAY)
    for burst in (1, 2):
        for member in sorted(members["household"]):
            account_id = members["household"][member]
            extra_refunds.append(
                refund(
                    account_orders[account_id][burst],
                    as_of + burst * DAY + rng.randint(0, available_jitter),
                )
            )
    append_as_of = as_of + 2 * DAY + 3600
    base_records = SourceRecords(
        accounts=accounts,
        devices=[Device(id=id_) for id_ in device_ids],
        addresses=[Address(id=id_, label=f"Synthetic address {id_[5:]}") for id_ in address_ids],
        orders=orders,
        refunds=base_refunds,
    )
    base = make_batch(base_records, seed=seed, as_of=as_of, mode="base")
    append = make_batch(
        SourceRecords(refunds=extra_refunds), seed=seed, as_of=append_as_of, mode="append"
    )
    validate_batch(base)
    validate_batch(append, base=base)

    # This is evaluation output only; operational manifests never reference it.
    truth = {
        "schema_version": "1.0",
        "dataset_id": "ringlens_demo_v1",
        "seed": seed,
        "base_as_of": as_of,
        "append_as_of": append_as_of,
        "window_seconds": WINDOW_SECONDS,
        "burst_window_seconds": 1800,
        "scenarios": {},
    }
    for group in members:
        account_ids = sorted(members[group].values())
        seed_id = refund_ids[group][0]
        first_expansion = account_ids
        if group == "ordinary":
            seed_account = next(row.account_id for row in base_refunds if row.id == seed_id)
            first_expansion = [seed_account]
        if group == "pattern_b":
            first_expansion = sorted(members[group][i] for i in (0, 1, 2))
        truth["scenarios"][group] = {
            "account_ids": account_ids,
            "base_refund_ids": sorted(refund_ids[group]),
            "seed_refund_id": seed_id,
            "expected_accounts_after_expansion_1": first_expansion,
            "expected_accounts_after_expansion_2": account_ids
            if group != "ordinary"
            else first_expansion,
            "expected_base_burst_count": 3 if group in ("pattern_a", "pattern_b") else 0,
            "expected_refund_rate": {
                "numerator": 3 if group.startswith("pattern_") else 1,
                "denominator": 5,
            },
        }
    truth["scenarios"]["ordinary"]["expected_refund_rate"] = {
        "numerator": 12,
        "denominator": 190,
    }
    truth["scenarios"]["ordinary"].update(
        {
            "expected_refunded_accounts": 12,
            "expected_unrefunded_accounts": 26,
            "expected_refunded_account_rate": {"numerator": 1, "denominator": 5},
        }
    )
    truth["scenarios"]["household"].update(
        {
            "expected_base_behavior": "shared device/address without a three-account refund burst",
            "append_refund_ids": sorted(row.id for row in extra_refunds),
            "expected_append_burst_count": 2,
            "expected_total_burst_count_after_append": 2,
            "expected_refund_rate_after_append": {"numerator": 3, "denominator": 5},
        }
    )
    return SyntheticDemo(base, append, truth)


def write_demo(
    demo: SyntheticDemo, *, output: Path, evaluation_output: Path, overwrite: bool = False
) -> None:
    output, evaluation_output = Path(output), Path(evaluation_output)
    if evaluation_output.resolve().is_relative_to(output.resolve()):
        raise DatasetIOError("Evaluation output must be outside the operational output directory")
    if "evaluation" not in evaluation_output.resolve().parts[:-1]:
        raise DatasetIOError(
            "Evaluation output must live under an evaluation/ directory excluded by Docker"
        )
    outputs = {}
    for name, batch in (("base", demo.base), ("new_evidence", demo.new_evidence)):
        directory = output / name
        if directory.exists() and {entry.name for entry in directory.iterdir()} - BATCH_FILENAMES:
            raise DatasetIOError(
                "Operational batch directory contains files outside the data contract"
            )
        outputs.update(
            {directory / filename: payload for filename, payload in batch_bytes(batch).items()}
        )
    outputs[evaluation_output] = (canonical_json(demo.ground_truth) + "\n").encode("utf-8")
    write_outputs(outputs, overwrite=overwrite)
