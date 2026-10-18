"""CSV boundary checks run without FalkorDB or an LLM."""

import csv
import io
import zipfile

from ringlens.csv_adapter import (
    CSVLimits,
    CSVSource,
    csv_bundle,
    error_report,
    required_columns,
    validate_csv_sources,
)
from ringlens.models import FILE_KINDS


def source(kind, header, rows, *, name=None, mapping=None, ignored=()):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return CSVSource(name or f"{kind}.csv", kind, output.getvalue().encode(), mapping, ignored)


def minimal():
    return [
        source("accounts", ["id", "created_at"], [["acct_0001", "2026-10-01T00:00:00Z"]]),
        source("devices", ["id"], [["dev_0001"]]),
        source("addresses", ["id", "label"], [["addr_0001", "Synthetic address A01"]]),
        source(
            "orders",
            required_columns("orders"),
            [
                [
                    "ord_0001",
                    "acct_0001",
                    "dev_0001",
                    "addr_0001",
                    "2026-10-02T00:00:00+00:00",
                    "12345",
                    "INR",
                ]
            ],
        ),
    ]


AS_OF = 1791072000  # 2026-10-04T00:00:00Z


def test_templates_and_sample_match_models():
    for sample in (False, True):
        with zipfile.ZipFile(io.BytesIO(csv_bundle(sample=sample))) as archive:
            assert sorted(archive.namelist()) == sorted(f"{kind}.csv" for kind in FILE_KINDS)
            for kind in FILE_KINDS:
                rows = list(csv.reader(io.StringIO(archive.read(f"{kind}.csv").decode())))
                assert tuple(rows[0]) == required_columns(kind)
                assert len(rows) > (1 if sample else 0)
    with zipfile.ZipFile(io.BytesIO(csv_bundle(sample=True, append=True))) as archive:
        assert archive.namelist() == ["refunds.csv"]
        assert len(list(csv.reader(io.StringIO(archive.read("refunds.csv").decode())))) == 7


def test_valid_zero_refund_batch_and_leading_zero_ids():
    result = validate_csv_sources(minimal(), mode="base", as_of=AS_OF)
    assert result.valid
    assert result.batch.records.orders[0].amount_minor == 12345
    assert result.batch.records.accounts[0].id == "acct_0001"
    assert result.batch.records.refunds == []
    assert any(issue.severity == "WARNING" for issue in result.issues)


def test_mapping_and_explicit_extra_ignoring():
    rows = minimal()
    rows[0] = source(
        "accounts",
        ["customer", "created", "ignore_me"],
        [["acct_0001", "1790812800", "hello"]],
        mapping={"id": "customer", "created_at": "created"},
        ignored=("ignore_me",),
    )
    assert validate_csv_sources(rows, mode="base", as_of=AS_OF).valid
    rows[0] = CSVSource(rows[0].filename, rows[0].kind, rows[0].content, rows[0].mapping, ())
    assert not validate_csv_sources(rows, mode="base", as_of=AS_OF).valid


def test_invalid_rows_report_origin_and_no_batch():
    rows = minimal()
    rows[3] = source(
        "orders",
        required_columns("orders"),
        [
            [
                "ord_0001",
                "acct_missing",
                "dev_0001",
                "addr_0001",
                "2026-10-02T00:00:00Z",
                "12345",
                "INR",
            ]
        ],
    )
    result = validate_csv_sources(rows, mode="base", as_of=AS_OF)
    assert not result.valid and result.batch is None
    assert any(
        issue.filename == "orders.csv" and issue.row == 2 and "orphan" in issue.reason
        for issue in result.issues
    )


def test_bad_types_duplicate_headers_and_quoting_rejected():
    rows = minimal()
    rows[3] = source(
        "orders",
        required_columns("orders"),
        [
            [
                "ord_0001",
                "acct_0001",
                "dev_0001",
                "addr_0001",
                "2026-10-02T00:00:00",
                "123.45",
                "USD",
            ]
        ],
    )
    result = validate_csv_sources(rows, mode="base", as_of=AS_OF)
    assert {issue.column for issue in result.issues} >= {"placed_at", "amount_minor"}
    rows[0] = CSVSource("accounts.csv", "accounts", b"id,id\nacct_a,acct_b\n")
    assert (
        "Duplicate CSV headers"
        in validate_csv_sources(rows, mode="base", as_of=AS_OF).issues[0].reason
    )
    rows[0] = CSVSource("accounts.csv", "accounts", b'id,created_at\n"unterminated,1\n')
    assert not validate_csv_sources(rows, mode="base", as_of=AS_OF).valid


def test_duplicate_ids_and_refund_limits():
    rows = minimal()
    rows[0] = source(
        "accounts", ["id", "created_at"], [["acct_0001", "1790812800"], ["acct_0001", "1790812800"]]
    )
    assert any(
        "Duplicate" in i.reason for i in validate_csv_sources(rows, mode="base", as_of=AS_OF).issues
    )
    rows = minimal()
    rows.append(
        source(
            "refunds",
            required_columns("refunds"),
            [
                ["ref_1", "acct_0001", "ord_0001", "1790985600", "12345", "INR"],
                ["ref_2", "acct_0001", "ord_0001", "1790985601", "100", "INR"],
            ],
        )
    )
    assert not validate_csv_sources(rows, mode="base", as_of=AS_OF).valid


def test_error_report_formula_prefix_is_escaped():
    rows = minimal()
    rows[0] = CSVSource("=SUM(1,2).csv", "accounts", b"id,id\na,b\n")
    report = error_report(validate_csv_sources(rows, mode="base", as_of=AS_OF).issues).decode()
    assert "'=SUM" in report


def test_server_row_limit_is_enforced():
    rows = minimal()
    rows[0] = source(
        "accounts", ["id", "created_at"], [["acct_0001", "1790812800"], ["acct_0002", "1790812800"]]
    )
    limits = CSVLimits(_env_file=None, max_file_rows=1)
    result = validate_csv_sources(rows, mode="base", as_of=AS_OF, limits=limits)
    assert not result.valid
    assert any("Row limit exceeded" in issue.reason for issue in result.issues)


def test_timezone_free_input_requires_explicit_source_timezone():
    rows = minimal()
    rows[0] = source("accounts", ["id", "created_at"], [["acct_0001", "2026-10-01T00:00:00"]])
    assert not validate_csv_sources(rows, mode="base", as_of=AS_OF).valid
    accepted = validate_csv_sources(rows, mode="base", as_of=AS_OF, source_timezone="Asia/Kolkata")
    assert accepted.valid
    assert accepted.batch.records.accounts[0].created_at == 1790793000
