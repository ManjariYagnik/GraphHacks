"""The five standalone demo inputs honor the production source contract."""

import json
from importlib import import_module
from pathlib import Path

import pytest

from ringlens.dataset_io import batch_bytes, read_batch
from ringlens.models import FILE_KINDS


@pytest.fixture
def demo_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    return import_module("example.demo.demo")


@pytest.mark.parametrize(
    ("number", "base_counts", "append_count"),
    [
        ("01", (1, 1, 1, 5, 1), None),
        ("02", (3, 1, 1, 15, 3), None),
        ("03", (4, 1, 4, 20, 12), None),
        ("04", (5, 2, 5, 25, 15), None),
        ("05", (3, 1, 1, 15, 3), 6),
    ],
)
def test_examples_form_canonical_importable_batches(
    demo_module, tmp_path, number, base_counts, append_count
):
    example = demo_module.load_example(number)
    assert tuple(len(getattr(example.base.records, kind)) for kind in FILE_KINDS) == base_counts
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    for filename, payload in batch_bytes(example.base).items():
        (base_dir / filename).write_bytes(payload)
    assert read_batch(base_dir) == example.base
    if append_count is not None:
        assert example.append is not None
        assert len(example.append.records.refunds) == append_count
        append_dir = tmp_path / "append"
        append_dir.mkdir()
        for filename, payload in batch_bytes(example.append).items():
            (append_dir / filename).write_bytes(payload)
        assert read_batch(append_dir) == example.append
        assert len(example.final_records.refunds) == base_counts[-1] + append_count
    else:
        assert example.append is None


def test_dry_run_does_not_open_database(demo_module, monkeypatch, capsys):
    monkeypatch.setattr(
        demo_module,
        "GraphStore",
        lambda *args, **kwargs: pytest.fail("Dry-run attempted a database connection"),
    )
    assert demo_module.main(["--example", "04", "--dry-run"]) == 0
    assert "DRY RUN ONLY" in capsys.readouterr().out


def test_invalid_example_rejected_before_database(demo_module, tmp_path, monkeypatch):
    source = json.loads(demo_module.EXAMPLES["01"].read_text(encoding="utf-8"))
    source["base"]["records"]["orders"][0]["account_id"] = "acct_missing"
    target = tmp_path / "data.json"
    from ringlens.models import canonical_json

    target.write_text(canonical_json(source) + "\n", encoding="utf-8")
    monkeypatch.setitem(demo_module.EXAMPLES, "01", target)
    with pytest.raises(demo_module.DemoError, match="orphan"):
        demo_module.load_example("01")


def test_missing_provider_configuration_stops_before_graph_writes(demo_module, monkeypatch):
    def unavailable(_):
        raise demo_module.ProviderError("Provider configuration is unavailable.")

    monkeypatch.setattr(demo_module, "create_provider", unavailable)
    monkeypatch.setattr(
        demo_module,
        "GraphStore",
        lambda *args, **kwargs: pytest.fail("Provider preflight attempted a graph write"),
    )
    assert demo_module.main(["--example", "04", "--with-agent"]) == 1
