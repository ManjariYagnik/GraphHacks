"""Mocked native provider decisions, real FalkorDB/domain tools. No live LLM claim."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

from ringlens.agent import get_agent_run, run_agent
from ringlens.agent_provider import AgentSettings, ProviderError, ProviderTurn
from ringlens.agent_tools import AgentTools
from ringlens.cases import apply_case_policy, get_case
from ringlens.config import Settings
from ringlens.graph_store import GraphStore
from ringlens.ingest import ingest_directory
from ringlens.investigation_models import InvestigationConfig, ScoringConfig
from ringlens.investigator import investigate
from ringlens.review_models import ReviewRequest
from ringlens.reviews import record_review

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
BASE, APPEND = ROOT / "data/demo/base", ROOT / "data/demo/new_evidence"
HOUSE, PATTERN = "ref_4d44e5cc3e0a8dae77fd", "ref_2b42ec18e21a70bdfa15"


@pytest.fixture
def graph():
    name = f"ringlens_test_agent_{uuid4().hex}"
    settings = Settings(graph_name=name)
    with GraphStore(settings) as store:
        assert not store.graph_exists(), "Refusing an existing graph"
        try:
            ingest_directory(store, BASE)
            yield store, settings
        finally:
            if name in store._db.list_graphs():
                store._db.select_graph(name).delete()


def call(name, arguments, index=0):
    item = {
        "type": "function_call",
        "name": name,
        "arguments": json.dumps(arguments),
        "call_id": f"call_{index}",
    }
    return ProviderTurn(items=[item], calls=[item])


class ScriptedProvider:
    """Native-shaped calls, with handles taken exclusively from actual tool output."""

    def __init__(self, seed=PATTERN, before_write=None, force_write=False):
        self.seed, self.before_write, self.force_write = seed, before_write, force_write
        self.index = 0

    def next_turn(self, history, schemas, mode):
        assert {s["name"] for s in schemas} == {
            "analyze_refund",
            "get_evidence",
            "get_review_context",
            "apply_case_policy",
            "get_case",
        }
        i = self.index
        self.index += 1
        outputs = [
            json.loads(h["output"]) for h in history if h.get("type") == "function_call_output"
        ]
        if i == 0:
            return call(
                "analyze_refund", {"refund_id": self.seed, "as_of": None, "window_days": None}, i
            )
        analysis = outputs[0]
        if analysis["status"] != "OK":
            return ProviderTurn(finished=True)
        ref = analysis["data"]["assessment_reference"]
        if i == 1:
            return call("get_review_context", {"assessment_reference": ref}, i)
        if i == 2:
            return call(
                "get_evidence",
                {"assessment_reference": ref, "account_id": None, "rule_id": "R1"},
                i,
            )
        if i == 3 and (mode == "investigate" or self.force_write):
            if self.before_write:
                self.before_write()
            return call("apply_case_policy", {"assessment_reference": ref}, i)
        # Untrusted prose must not become a final case claim.
        return ProviderTurn(
            items=[{"type": "message", "content": "Case FAKE opened."}], finished=True
        )


def test_real_tools_and_grounded_case_actions_repeat_and_lookup(graph):
    store, settings = graph
    first = run_agent(
        store, "Investigate and open a case", mode="investigate", provider=ScriptedProvider()
    )
    assert first.status == "COMPLETE", first.errors
    assert [a["action"] for a in first.activities if a["tool"] == "apply_case_policy"] == [
        "CREATED"
    ]
    assert "score=90" in first.response and "FAKE" not in first.response
    case_id = next(id_ for id_ in first.references if id_.startswith("inv_"))
    assert get_case(store, case_id).investigation.status == "OPEN"
    repeated = run_agent(
        store, "Investigate again", mode="investigate", provider=ScriptedProvider()
    )
    assert repeated.status == "COMPLETE" and "EXISTING_UNCHANGED" in repeated.response
    with GraphStore(settings) as restarted:
        audit = get_agent_run(restarted, first.run_id)
        assert audit["status"] == "COMPLETE" and audit["case_ids"] == [case_id]
        assert "arguments" not in audit["activity_json"] and "reasoning" not in audit
        tools = AgentTools(restarted)
        looked_up = tools.execute("get_case", {"case_id": case_id})
        assert looked_up["status"] == "OK" and looked_up["data"]["assessments"]
    assert all(
        store.read("MATCH (n {id: $id}) RETURN count(n)", {"id": id_}).result_set == [[1]]
        for id_ in first.references
        if id_.startswith(("ev_", "inv_", "asm_", "ref_", "acct_"))
    )
    ingest_directory(store, APPEND)

    class HistoricalCaseProvider:
        def next_turn(self, history, schemas, mode):
            outputs = [
                json.loads(h["output"]) for h in history if h.get("type") == "function_call_output"
            ]
            if not outputs:
                return call("get_case", {"case_id": case_id})
            if len(outputs) == 1:
                reference = outputs[0]["data"]["assessments"][0]["id"]
                return call(
                    "get_evidence",
                    {"assessment_reference": reference, "rule_id": "R1", "account_id": None},
                    1,
                )
            return ProviderTurn(finished=True)

    explained = run_agent(
        store, "Explain the previous case evidence", provider=HistoricalCaseProvider()
    )
    assert explained.status == "COMPLETE", explained.errors
    assert "Historical snapshot" in explained.response
    assert case_id in explained.references


def test_assess_permissions_are_enforced_even_for_malicious_model(graph):
    store, _ = graph
    result = run_agent(store, "Explain only", provider=ScriptedProvider(force_write=True))
    assert "Assess mode cannot" in result.response
    assert store.read("MATCH (i:Investigation) RETURN count(i)").result_set == [[0]]
    assert store.read("MATCH (a:Assessment) RETURN count(a)").result_set == [[0]]
    assert store.read("MATCH (r:Review) RETURN count(r)").result_set == [[0]]
    assert store.read("MATCH (r:AgentRun) RETURN count(r)").result_set == [[1]]


def test_fresh_agent_session_uses_saved_human_memory_then_new_bursts(graph):
    store, settings = graph
    review = record_review(
        store,
        ReviewRequest(
            refund_id=HOUSE,
            reviewer_id="human_demo",
            outcome="BENIGN_EXPLANATION",
            explanation="Shared device/address explained; future refunds are not cleared.",
            rule_ids=["R1", "R5"],
        ),
    )
    with GraphStore(settings) as fresh:
        initial = run_agent(
            fresh,
            "Investigate household refund",
            mode="investigate",
            provider=ScriptedProvider(HOUSE),
        )
    assert initial.status == "COMPLETE" and "ALREADY_REVIEWED" in initial.response
    assert review.id in initial.references and "score=35" in initial.response
    ingest_directory(store, APPEND)
    with GraphStore(settings) as fresh:
        changed = run_agent(
            fresh,
            "Investigate same refund again",
            mode="investigate",
            provider=ScriptedProvider(HOUSE),
        )
    assert changed.status == "COMPLETE", changed.errors
    assert "CREATED" in changed.response and "score=100" in changed.response
    assert review.id in changed.references and "New/unexplained R2" in changed.response
    case_id = next(id_ for id_ in changed.references if id_.startswith("inv_"))
    lookup = AgentTools(store).execute("get_case", {"case_id": case_id})
    assert lookup["data"]["scoped_reviews"][0]["id"] == review.id


def test_stale_analysis_cannot_apply_policy(graph):
    store, _ = graph
    result = run_agent(
        store,
        "Open case",
        mode="investigate",
        provider=ScriptedProvider(before_write=lambda: ingest_directory(store, APPEND)),
    )
    assert result.status == "INCOMPLETE"
    assert "NOT_CREATED_INCOMPLETE" in result.response
    assert store.read("MATCH (i:Investigation) RETURN count(i)").result_set == [[0]]


def test_agent_reopens_closed_case_and_preserves_review(graph):
    store, _ = graph
    config = InvestigationConfig(scoring=ScoringConfig(r1_points=40, r3_points=10, r4_points=0))
    original = apply_case_policy(store, investigate(store, HOUSE, config=config))
    review = record_review(
        store,
        ReviewRequest(
            refund_id=HOUSE,
            reviewer_id="human_demo",
            outcome="BENIGN_EXPLANATION",
            explanation="Shared resources explained; close the case explicitly.",
            rule_ids=["R1", "R5"],
            case_id=original.case_id,
            case_status="CLOSED",
        ),
    )
    ingest_directory(store, APPEND)
    result = run_agent(
        store, "Investigate again", mode="investigate", provider=ScriptedProvider(HOUSE)
    )
    assert result.status == "COMPLETE" and "Case action: REOPENED" in result.response
    assert original.case_id in result.references and review.id in result.references
    assert get_case(store, original.case_id).investigation.status == "REOPENED"


def test_incomplete_graph_discovery_never_becomes_low_or_creates_case(graph, monkeypatch):
    store, _ = graph
    monkeypatch.setattr(
        "ringlens.agent_tools.investigate",
        lambda s, r, **kw: investigate(s, r, config=InvestigationConfig(max_accounts=1)),
    )
    result = run_agent(store, "Investigate", mode="investigate", provider=ScriptedProvider())
    assert result.status == "INCOMPLETE" and "priority=LOW" not in result.response
    assert store.read("MATCH (i:Investigation) RETURN count(i)").result_set == [[0]]


def test_invalid_tool_inputs_unknown_handles_and_missing_refund(graph):
    store, _ = graph
    tools = AgentTools(store, mode="investigate")
    for name, params in [
        ("analyze_refund", {"refund_id": PATTERN, "score": 100}),
        ("analyze_refund", {"refund_id": PATTERN, "window_days": True}),
        ("analyze_refund", {"refund_id": "ref_missing"}),
        ("get_review_context", {"assessment_reference": "asm_" + "0" * 64}),
        ("record_review", {}),
    ]:
        assert tools.execute(name, params)["status"] == "ERROR"
    analysis = tools.execute("analyze_refund", {"refund_id": PATTERN})
    ref = analysis["data"]["assessment_reference"]
    assert tools.execute("apply_case_policy", {"assessment_reference": ref})["status"] == "ERROR"
    assert store.read("MATCH (i:Investigation) RETURN count(i)").result_set == [[0]]


@pytest.mark.parametrize("failure", ["provider", "loop", "calls"])
def test_provider_errors_and_limits_are_explicit(graph, failure):
    store, _ = graph

    class BrokenProvider:
        def next_turn(self, history, schemas, mode):
            if failure == "provider":
                raise ProviderError("Controlled provider unavailable.")
            if failure == "calls":
                return call("unknown_tool", {}, len(history))
            return ProviderTurn()

    settings = AgentSettings(agent_max_turns=2, agent_max_tool_calls=1)
    result = run_agent(store, "Explain", provider=BrokenProvider(), settings=settings)
    assert result.status in {"FAILED", "INCOMPLETE"} and result.errors
    assert get_agent_run(store, result.run_id)["status"] == result.status
    assert store.read("MATCH (i:Investigation) RETURN count(i)").result_set == [[0]]
