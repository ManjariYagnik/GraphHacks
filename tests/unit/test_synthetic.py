"""Independent fixture topology/time checks, never a production scoring engine."""

import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from ringlens.cli import app
from ringlens.dataset_io import DatasetIOError, batch_bytes, load_dataset
from ringlens.synthetic import generate_demo, parse_as_of, write_demo
from ringlens.validation import RecordValidationError

AS_OF = 1792058400  # 2026-10-15T10:00:00Z; fixed rather than derived from a clock.
DAY = 86400
KINDS = ("accounts", "devices", "addresses", "orders", "refunds")
ROOT = Path(__file__).resolve().parents[2]


def raw_records(directory):
    # Independent JSON reader: scenario checks do not trust the generation models.
    return {
        kind: [json.loads(line) for line in (directory / f"{kind}.jsonl").read_bytes().splitlines()]
        for kind in KINDS
    }


def expand_accounts(records, initial, as_of):
    eligible = [row for row in records["orders"] if as_of - 30 * DAY <= row["placed_at"] <= as_of]
    devices = {row["device_id"] for row in eligible if row["account_id"] in initial}
    return set(initial) | {row["account_id"] for row in eligible if row["device_id"] in devices}


def burst_dates(refunds, accounts, as_of):
    by_date = defaultdict(list)
    for row in refunds:
        if row["account_id"] in accounts and as_of - 30 * DAY <= row["requested_at"] <= as_of:
            by_date[row["requested_at"] // DAY].append(row)
    result = set()
    for date, rows in by_date.items():
        for start in rows:
            participants = {
                row["account_id"]
                for row in rows
                if 0 <= row["requested_at"] - start["requested_at"] <= 1800
            }
            if len(participants) >= 3:
                result.add(date)
    return result


def assert_scenarios(base, extra, truth):
    assert [len(base[kind]) for kind in KINDS] == [50, 42, 46, 250, 42]
    assert [len(extra[kind]) for kind in KINDS] == [0, 0, 0, 0, 6]
    as_of, advanced = truth["base_as_of"], truth["append_as_of"]
    assert advanced == as_of + 2 * DAY + 3600
    accounts = {row["id"]: row for row in base["accounts"]}
    orders = {row["id"]: row for row in base["orders"]}
    devices = {row["id"] for row in base["devices"]}
    addresses = {row["id"] for row in base["addresses"]}
    assert len(accounts) == 50 and len(orders) == 250
    assert Counter(row["account_id"] for row in orders.values()) == Counter(
        dict.fromkeys(accounts, 5)
    )
    amounts = {row["amount_minor"] for row in orders.values()}
    assert len(amounts) > 100
    for row in orders.values():
        assert row["account_id"] in accounts
        assert row["device_id"] in devices and row["address_id"] in addresses
        assert accounts[row["account_id"]]["created_at"] < row["placed_at"]
        # Every order remains in both horizons, with a multi-day boundary margin.
        assert as_of - 26 * DAY < row["placed_at"] < as_of - DAY
        assert advanced - 30 * DAY <= row["placed_at"] <= advanced
        assert type(row["amount_minor"]) is int and row["amount_minor"] > 0
        assert row["currency"] == "INR"
    all_refunds = base["refunds"] + extra["refunds"]
    assert len({row["id"] for row in all_refunds}) == 48
    assert len({row["order_id"] for row in all_refunds}) == 48
    for row in all_refunds:
        order = orders[row["order_id"]]
        assert row["account_id"] == order["account_id"]
        assert row["requested_at"] > order["placed_at"]
        assert row["currency"] == order["currency"] == "INR"
        assert 0 < row["amount_minor"] <= order["amount_minor"]
        assert type(row["requested_at"]) is int and type(row["amount_minor"]) is int
    assert all(as_of - 26 * DAY < row["requested_at"] < as_of - DAY / 4 for row in base["refunds"])
    assert all(as_of < row["requested_at"] < advanced for row in extra["refunds"])

    scenarios = truth["scenarios"]
    groups = {name: set(scenario["account_ids"]) for name, scenario in scenarios.items()}
    assert {name: len(members) for name, members in groups.items()} == {
        "ordinary": 38,
        "pattern_a": 4,
        "pattern_b": 5,
        "household": 3,
    }
    assert set.union(*groups.values()) == set(accounts)
    assert sum(map(len, groups.values())) == 50
    uses = defaultdict(set)
    device_users = defaultdict(set)
    ships = defaultdict(set)
    for row in orders.values():
        uses[row["account_id"]].add(row["device_id"])
        device_users[row["device_id"]].add(row["account_id"])
        ships[row["account_id"]].add(row["address_id"])
    for name, members in groups.items():
        scenario = scenarios[name]
        refunds = [row for row in base["refunds"] if row["account_id"] in members]
        assert set(row["id"] for row in refunds) == set(scenario["base_refund_ids"])
        counts = Counter(row["account_id"] for row in refunds)
        if name == "ordinary":
            assert len(refunds) == len(counts) == 12 and set(counts.values()) == {1}
            for account in members:
                assert len(uses[account]) == 1
                assert device_users[next(iter(uses[account]))] == {account}
            shared_addresses = Counter(next(iter(ships[account])) for account in members)
            assert sorted(shared_addresses.values()).count(2) == 2
            assert max(shared_addresses.values()) == 2
        else:
            fraction = 3 if name.startswith("pattern_") else 1
            assert counts == Counter(dict.fromkeys(members, fraction))
            assert scenario["expected_refund_rate"] == {"numerator": fraction, "denominator": 5}
        actual_bursts = burst_dates(base["refunds"], members, as_of)
        expected_bursts = 3 if name.startswith("pattern_") else 0
        assert len(actual_bursts) == scenario["expected_base_burst_count"] == expected_bursts
        for date in actual_bursts:
            burst = [row for row in refunds if row["requested_at"] // DAY == date]
            assert Counter(row["account_id"] for row in burst) == Counter(dict.fromkeys(members, 1))
            times = [row["requested_at"] for row in burst]
            assert max(times) - min(times) <= 1800
        seed_refund = next(row for row in refunds if row["id"] == scenario["seed_refund_id"])
        first = expand_accounts(base, {seed_refund["account_id"]}, as_of)
        second = expand_accounts(base, first, as_of)
        assert first == set(scenario["expected_accounts_after_expansion_1"])
        assert second == set(scenario["expected_accounts_after_expansion_2"])

    for name in ("pattern_a", "household"):
        members = groups[name]
        shared = set.union(*(uses[account] for account in members))
        assert len(shared) == 1 and device_users[next(iter(shared))] == members
    assert len(set.union(*(ships[a] for a in groups["pattern_a"]))) == 4
    assert len(set.union(*(ships[a] for a in groups["household"]))) == 1

    pattern = groups["pattern_b"]
    pattern_devices = set.union(*(uses[account] for account in pattern))
    assert len(pattern_devices) == 2
    assert sorted(len(uses[account]) for account in pattern) == [1, 1, 1, 1, 2]
    assert all(
        len(device_users[device]) == 3 and device_users[device] <= pattern
        for device in pattern_devices
    )
    seed = next(
        row for row in base["refunds"] if row["id"] == scenarios["pattern_b"]["seed_refund_id"]
    )
    seed_device = orders[seed["order_id"]]["device_id"]
    assert uses[seed["account_id"]] == {seed_device}  # A/B, not the bridge C.
    first = expand_accounts(base, {seed["account_id"]}, as_of)
    assert first == device_users[seed_device] and len(first) == 3
    assert len(pattern - first) == 2
    assert expand_accounts(base, first, as_of) == pattern

    household = groups["household"]
    assert not burst_dates(base["refunds"], household, as_of)
    assert not burst_dates(base["refunds"], groups["ordinary"] | household, as_of)
    assert {row["account_id"] for row in extra["refunds"]} == household
    assert not (
        {row["order_id"] for row in extra["refunds"]} & {row["order_id"] for row in base["refunds"]}
    )
    assert len(burst_dates(all_refunds, household, advanced)) == 2
    for day in (1, 2):
        burst = [
            row
            for row in extra["refunds"]
            if as_of + day * DAY <= row["requested_at"] <= as_of + day * DAY + 1800
        ]
        assert {row["account_id"] for row in burst} == household and len(burst) == 3
    assert Counter(row["account_id"] for row in all_refunds if row["account_id"] in household) == (
        Counter(dict.fromkeys(household, 3))
    )
    assert set(scenarios["household"]["append_refund_ids"]) == {
        row["id"] for row in extra["refunds"]
    }


@pytest.fixture
def generated(tmp_path):
    output = tmp_path / "data"
    evaluation = tmp_path / "evaluation" / "truth.json"
    write_demo(generate_demo(seed=42, as_of=AS_OF), output=output, evaluation_output=evaluation)
    return output, evaluation


@pytest.mark.parametrize("seed", [0, 1, 42, 43, 991, 2**63 - 1])
def test_generated_scenarios_are_independently_validated(tmp_path, seed):
    output, evaluation = tmp_path / "data", tmp_path / "evaluation" / "truth.json"
    write_demo(generate_demo(seed=seed, as_of=AS_OF), output=output, evaluation_output=evaluation)
    assert_scenarios(
        raw_records(output / "base"),
        raw_records(output / "new_evidence"),
        json.loads(evaluation.read_bytes()),
    )


def test_committed_demo_files_match_generation_and_scenarios():
    output, evaluation = ROOT / "data/demo", ROOT / "evaluation/demo_ground_truth.json"
    truth = json.loads(evaluation.read_bytes())
    assert truth["seed"] == 42 and truth["base_as_of"] == AS_OF
    assert_scenarios(raw_records(output / "base"), raw_records(output / "new_evidence"), truth)
    demo = generate_demo(seed=42, as_of=AS_OF)
    assert evaluation.read_bytes() == canonical(demo.ground_truth) + b"\n"
    for name, batch in (("base", demo.base), ("new_evidence", demo.new_evidence)):
        for filename, payload in batch_bytes(batch).items():
            assert (output / name / filename).read_bytes() == payload


def test_reproducible_bytes_and_no_unseeded_randomness(generated, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unseeded randomness must not be used")

    monkeypatch.setattr("random.random", forbidden)
    monkeypatch.setattr("random.shuffle", forbidden)
    output, evaluation = generated
    other, other_truth = tmp_path / "second", tmp_path / "evaluation/second.json"
    write_demo(generate_demo(seed=42, as_of=AS_OF), output=other, evaluation_output=other_truth)
    for file in output.rglob("*"):
        if file.is_file():
            assert file.read_bytes() == (other / file.relative_to(output)).read_bytes()
    assert evaluation.read_bytes() == other_truth.read_bytes()
    changed = generate_demo(seed=43, as_of=AS_OF)
    original = generate_demo(seed=42, as_of=AS_OF)
    assert changed.base.manifest.batch_id != original.base.manifest.batch_id
    assert batch_bytes(changed.base)["orders.jsonl"] != batch_bytes(original.base)["orders.jsonl"]


def test_overwrite_is_explicit_and_conflicts_are_checked_before_any_write(generated):
    output, evaluation = generated
    before = {file: file.read_bytes() for file in output.rglob("*") if file.is_file()}
    before[evaluation] = evaluation.read_bytes()
    write_demo(generate_demo(seed=42, as_of=AS_OF), output=output, evaluation_output=evaluation)
    with pytest.raises(DatasetIOError, match="--overwrite"):
        write_demo(generate_demo(seed=43, as_of=AS_OF), output=output, evaluation_output=evaluation)
    assert all(file.read_bytes() == payload for file, payload in before.items())
    write_demo(
        generate_demo(seed=43, as_of=AS_OF),
        output=output,
        evaluation_output=evaluation,
        overwrite=True,
    )
    assert json.loads(evaluation.read_bytes())["seed"] == 43
    load_dataset(output / "base")
    load_dataset(output / "new_evidence", base_directory=output / "base")


def test_runtime_has_no_scenario_labels_or_extra_fields(generated):
    output, evaluation = generated
    expected_fields = {
        "accounts": {"id", "created_at"},
        "devices": {"id"},
        "addresses": {"id", "label"},
        "orders": {
            "id",
            "account_id",
            "device_id",
            "address_id",
            "placed_at",
            "amount_minor",
            "currency",
        },
        "refunds": {"id", "account_id", "order_id", "requested_at", "amount_minor", "currency"},
    }
    for name in ("base", "new_evidence"):
        for kind, rows in raw_records(output / name).items():
            for row in rows:
                assert set(row) == expected_fields[kind]
                assert re.fullmatch(r"(?:acct|dev|addr|ord|ref)_[0-9a-f]{20}", row["id"])
                if kind == "addresses":
                    assert re.fullmatch(r"Synthetic address [0-9a-f]{20}", row["label"])
        for file in (output / name).iterdir():
            text = file.read_text().lower()
            assert not any(
                marker in text
                for marker in (
                    "pattern_",
                    "ordinary",
                    "household",
                    "scenario",
                    "fraud",
                    "risk_score",
                    "ring device",
                    "ground_truth",
                    "evaluation/",
                )
            )
    assert not evaluation.is_relative_to(output)


def test_append_needs_base_and_resolves_external_references(generated):
    output, _ = generated
    load_dataset(output / "base")
    with pytest.raises(DatasetIOError, match="--base"):
        load_dataset(output / "new_evidence")
    load_dataset(output / "new_evidence", base_directory=output / "base")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def resign_manifest(directory, **changes):
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest.update(changes)
    descriptor = {key: value for key, value in manifest.items() if key != "batch_id"}
    manifest["batch_id"] = "batch_" + sha256(canonical(descriptor)).hexdigest()
    (directory / "manifest.json").write_bytes(canonical(manifest) + b"\n")


def replace_file(directory, kind, payload, *, count=None):
    filename = f"{kind}.jsonl"
    (directory / filename).write_bytes(payload)
    files = json.loads((directory / "manifest.json").read_bytes())["files"]
    entry = next(file for file in files if file["kind"] == kind)
    entry["sha256"] = sha256(payload).hexdigest()
    if count is not None:
        entry["count"] = count
    resign_manifest(directory, files=files)


@pytest.mark.parametrize(
    "tamper",
    ["reverse", "duplicate", "unknown", "boolean", "crlf", "no_lf", "blank", "duplicate_key"],
)
def test_reader_rejects_rehashed_invalid_rows(generated, tamper):
    output, _ = generated
    directory = output / "base"
    rows = (directory / "accounts.jsonl").read_bytes().splitlines(keepends=True)
    count = len(rows)
    if tamper == "reverse":
        rows.reverse()
    elif tamper == "duplicate":
        rows.insert(0, rows[0])
        count += 1
    elif tamper in ("unknown", "boolean"):
        record = json.loads(rows[0])
        record.update({"risk_score": 90} if tamper == "unknown" else {"created_at": True})
        rows[0] = canonical(record) + b"\n"
    elif tamper == "crlf":
        rows[0] = rows[0].replace(b"\n", b"\r\n")
    elif tamper == "no_lf":
        rows[-1] = rows[-1].rstrip(b"\n")
    elif tamper == "blank":
        rows.insert(0, b"\n")
    else:
        record = json.loads(rows[0])
        rows[0] = (
            f'{{"created_at":{record["created_at"]},"id":"{record["id"]}","id":"{record["id"]}"}}\n'
        ).encode()
    replace_file(directory, "accounts", b"".join(rows), count=count)
    with pytest.raises(DatasetIOError):
        load_dataset(directory)


def test_reader_checks_digest_counts_manifest_identity_and_symlinks(generated, tmp_path):
    output, _ = generated
    directory = output / "base"
    original = (directory / "devices.jsonl").read_bytes()
    (directory / "devices.jsonl").write_bytes(original + b"\n")
    with pytest.raises(DatasetIOError, match="SHA-256"):
        load_dataset(directory)
    (directory / "devices.jsonl").write_bytes(original)
    outside = tmp_path / "external.jsonl"
    outside.write_bytes(original)
    (directory / "devices.jsonl").unlink()
    (directory / "devices.jsonl").symlink_to(outside)
    with pytest.raises(DatasetIOError, match="symbolic links"):
        load_dataset(directory)


def test_reader_rejects_resigned_count_mismatch(generated):
    output, _ = generated
    directory = output / "base"
    files = json.loads((directory / "manifest.json").read_bytes())["files"]
    files[0]["count"] += 1
    resign_manifest(directory, files=files)
    with pytest.raises(DatasetIOError, match="row count"):
        load_dataset(directory)


def test_reader_rejects_tampered_batch_identity(generated):
    output, _ = generated
    directory = output / "base"
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["seed"] += 1  # Leave the previous batch ID to demonstrate tamper detection.
    (directory / "manifest.json").write_bytes(canonical(manifest) + b"\n")
    with pytest.raises(ValueError, match="content hash"):
        load_dataset(directory)


def test_reader_rejects_missing_or_unmanifested_files(generated):
    output, _ = generated
    extra = output / "base/unmanifested.json"
    extra.write_bytes(b"{}\n")
    with pytest.raises(DatasetIOError, match="exactly"):
        load_dataset(output / "base")
    extra.unlink()
    (output / "base/addresses.jsonl").unlink()
    with pytest.raises(DatasetIOError, match="exactly"):
        load_dataset(output / "base")


def test_append_allows_identical_immutable_reference_replay(generated):
    output, _ = generated
    base, extra = output / "base", output / "new_evidence"
    record = (base / "accounts.jsonl").read_bytes().splitlines(keepends=True)[0]
    replace_file(extra, "accounts", record, count=1)
    load_dataset(extra, base_directory=base)


def test_file_validation_never_reads_evaluation(generated, monkeypatch):
    output, evaluation = generated
    read_bytes = Path.read_bytes

    def guarded_read(self):
        if self == evaluation:
            raise AssertionError("runtime file validation read evaluation data")
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    load_dataset(output / "new_evidence", base_directory=output / "base")


def test_bytes_are_stable_across_interpreter_hash_seeds():
    script = """
import json
from hashlib import sha256
from ringlens.synthetic import generate_demo
from ringlens.dataset_io import batch_bytes
from ringlens.models import canonical_json
demo = generate_demo(seed=42, as_of=1792058400)
result = {name: {key: sha256(value).hexdigest() for key, value in batch_bytes(batch).items()}
          for name, batch in (("base", demo.base), ("append", demo.new_evidence))}
result["evaluation"] = sha256(canonical_json(demo.ground_truth).encode()).hexdigest()
print(json.dumps(result, sort_keys=True))
"""
    results = []
    for hash_seed in ("1", "99"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        result = subprocess.run(
            [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True
        )
        results.append(result.stdout)
    assert results[0] == results[1]


def test_append_rejects_orphans_conflicts_duplicate_refunds_and_backward_horizon(generated):
    output, _ = generated
    base, extra = output / "base", output / "new_evidence"
    original = (extra / "refunds.jsonl").read_bytes()
    original_manifest = (extra / "manifest.json").read_bytes()
    base_refunds = raw_records(base)["refunds"]
    for problem in ("orphan", "conflict", "duplicate_order"):
        rows = [json.loads(line) for line in original.splitlines()]
        row = rows[0]
        previous = next(
            refund for refund in base_refunds if refund["account_id"] == row["account_id"]
        )
        if problem == "orphan":
            row["order_id"] = "ord_missing"
        elif problem == "conflict":
            row["id"] = previous["id"]
        else:
            row.update(order_id=previous["order_id"], amount_minor=1)
        payload = b"".join(
            canonical(row) + b"\n" for row in sorted(rows, key=lambda row: row["id"])
        )
        replace_file(extra, "refunds", payload)
        with pytest.raises((DatasetIOError, RecordValidationError)):
            load_dataset(extra, base_directory=base)
        (extra / "refunds.jsonl").write_bytes(original)
        (extra / "manifest.json").write_bytes(original_manifest)
    resign_manifest(extra, as_of=AS_OF - 1)
    with pytest.raises(DatasetIOError, match="backward"):
        load_dataset(extra, base_directory=base)


@pytest.mark.parametrize(
    "timestamp",
    ["2026-10-15", "2026-10-15T10:00:00", "2026-10-15T10:00:00+05:30", "2026-10-15T10:00:00.1Z"],
)
def test_as_of_requires_explicit_whole_second_utc(timestamp):
    with pytest.raises(ValueError, match="UTC"):
        parse_as_of(timestamp)


@pytest.mark.parametrize("as_of", [AS_OF + 12345, AS_OF - AS_OF % DAY + DAY - 1])
def test_other_as_of_times_keep_all_scenario_guarantees(tmp_path, as_of):
    output, evaluation = tmp_path / "data", tmp_path / "evaluation/truth.json"
    write_demo(generate_demo(seed=42, as_of=as_of), output=output, evaluation_output=evaluation)
    assert_scenarios(
        raw_records(output / "base"),
        raw_records(output / "new_evidence"),
        json.loads(evaluation.read_bytes()),
    )


def test_cli_generation_and_validation_need_no_settings_or_database(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ringlens.cli.GraphStore", MagicMock(side_effect=AssertionError("database used"))
    )
    monkeypatch.setattr(
        "ringlens.cli.Settings", MagicMock(side_effect=AssertionError("settings used"))
    )
    output, evaluation = tmp_path / "data", tmp_path / "evaluation/truth.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "data",
            "generate",
            "--seed",
            "42",
            "--as-of",
            "2026-10-15T10:00:00Z",
            "--output",
            str(output),
            "--evaluation-output",
            str(evaluation),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "250 orders, 42 refunds" in result.output
    assert runner.invoke(app, ["data", "validate", "--input", str(output / "base")]).exit_code == 0
    no_context = runner.invoke(app, ["data", "validate", "--input", str(output / "new_evidence")])
    assert no_context.exit_code == 2 and "--base" in no_context.output
    assert (
        runner.invoke(
            app,
            [
                "data",
                "validate",
                "--input",
                str(output / "new_evidence"),
                "--base",
                str(output / "base"),
            ],
        ).exit_code
        == 0
    )


def test_evaluation_cannot_be_written_into_operational_data(tmp_path):
    demo = generate_demo(seed=42, as_of=AS_OF)
    with pytest.raises(DatasetIOError, match="outside"):
        write_demo(
            demo,
            output=tmp_path / "data",
            evaluation_output=tmp_path / "data/evaluation/truth.json",
        )
    with pytest.raises(DatasetIOError, match="evaluation/"):
        write_demo(demo, output=tmp_path / "data", evaluation_output=tmp_path / "truth.json")
    assert not (tmp_path / "data").exists()


def test_invalid_evaluation_parent_does_not_partially_write_runtime_files(tmp_path):
    (tmp_path / "evaluation").write_text("existing file")
    with pytest.raises(DatasetIOError, match="parent path"):
        write_demo(
            generate_demo(seed=42, as_of=AS_OF),
            output=tmp_path / "data",
            evaluation_output=tmp_path / "evaluation/truth.json",
        )
    assert not (tmp_path / "data").exists()
