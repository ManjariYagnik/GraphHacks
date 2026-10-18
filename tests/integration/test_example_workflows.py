"""Live examples use disposable FalkorDB graphs and verify persistence."""

import json
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest

from ringlens.agent_provider import AgentSettings, ProviderTurn
from ringlens.config import Settings
from ringlens.graph_store import GraphStore
from ringlens.investigator import investigate

pytestmark = pytest.mark.integration


@pytest.fixture
def demo_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    return import_module("example.demo.demo")


@pytest.mark.parametrize(
    ("number", "score", "priority", "candidates", "maximum_depth", "refunds", "action"),
    [
        ("01", 0, "LOW", 1, 0, 1, "NO_CASE_REQUIRED"),
        ("02", 35, "LOW", 3, 1, 3, "NO_CASE_REQUIRED"),
        ("03", 90, "HIGH", 4, 1, 12, "CREATED"),
        ("04", 90, "HIGH", 5, 2, 15, "CREATED"),
        ("05", 100, "HIGH", 3, 1, 9, "CREATED"),
    ],
)
def test_live_examples_persist_and_read_back(
    demo_module, number, score, priority, candidates, maximum_depth, refunds, action
):
    graph_name = "ringlens_example_test_" + uuid4().hex
    settings = Settings(graph_name=graph_name)
    try:
        kwargs = {}
        if number == "05":
            kwargs = {
                "reviewer_id": "example-test-reviewer",
                "review_outcome": "BENIGN_EXPLANATION",
                "review_explanation": (
                    "Shared device and address were reviewed; later events are not covered."
                ),
            }
        outcome = demo_module.run_live(
            demo_module.load_example(number), graph_name=graph_name, **kwargs
        )
        assert (
            outcome.score,
            outcome.priority,
            outcome.candidate_count,
            outcome.maximum_depth,
            outcome.refund_count,
            outcome.case_action,
        ) == (score, priority, candidates, maximum_depth, refunds, action)
        assert bool(outcome.case_id) == (priority != "LOW")
        assert bool(outcome.review_id) == (number == "05")
        if number == "04":
            with GraphStore(settings) as store:
                assessment = investigate(store, demo_module.load_example(number).seed_refund_id)
                assert [
                    sum(candidate.depth == depth for candidate in assessment.candidates)
                    for depth in (0, 1, 2)
                ] == [1, 2, 2]
            with pytest.raises(demo_module.DemoError, match="already exists"):
                demo_module.run_live(demo_module.load_example(number), graph_name=graph_name)
    finally:
        with GraphStore(settings) as store:
            if graph_name in store._db.list_graphs():
                store._db.select_graph(graph_name).delete()


def test_optional_agent_path_uses_real_graph_tools_with_mocked_provider(demo_module):
    graph_name = "ringlens_example_test_" + uuid4().hex
    settings = Settings(graph_name=graph_name)
    example = demo_module.load_example("01")

    class Provider:
        def next_turn(self, history, schemas, mode):
            outputs = [
                json.loads(item["output"])
                for item in history
                if item.get("type") == "function_call_output"
            ]
            if not outputs:
                name, arguments = "analyze_refund", {"refund_id": example.seed_refund_id}
            elif len(outputs) == 1:
                name, arguments = (
                    "get_review_context",
                    {"assessment_reference": outputs[0]["data"]["assessment_reference"]},
                )
            else:
                return ProviderTurn(finished=True)
            call = {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps(arguments),
                "call_id": f"call_{len(outputs)}",
            }
            return ProviderTurn(items=[call], calls=[call])

    try:
        outcome = demo_module.run_live(
            example,
            graph_name=graph_name,
            agent_provider=Provider(),
            agent_settings=AgentSettings(_env_file=None, agent_model="mock-model"),
        )
        assert outcome.case_action == "NO_CASE_REQUIRED"
        with GraphStore(settings) as store:
            assert store.read("MATCH (r:AgentRun) RETURN r.status").result_set == [["COMPLETE"]]
    finally:
        with GraphStore(settings) as store:
            if graph_name in store._db.list_graphs():
                store._db.select_graph(graph_name).delete()
