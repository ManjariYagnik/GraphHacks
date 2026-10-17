"""Official SDK over a fake HTTP transport: no live provider calls or credentials."""

import json
from hashlib import sha256

import httpx
import openai
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from ringlens.agent_provider import AgentSettings, OpenAIProvider, ProviderError
from ringlens.agent_tools import AgentTools, tool_schemas
from ringlens.cli import app
from ringlens.models import canonical_json
from ringlens.review_models import EvidenceFact, ReviewRequest
from ringlens.reviews import _scope_covers


def test_missing_credentials_model_and_secret_hiding(monkeypatch):
    monkeypatch.setenv("RINGLENS_AGENT_PROVIDER", "openai")
    monkeypatch.delenv("RINGLENS_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RINGLENS_AGENT_MODEL", raising=False)
    settings = AgentSettings(_env_file=None)
    with pytest.raises(ProviderError, match="RINGLENS_OPENAI_API_KEY"):
        OpenAIProvider(settings)
    settings = AgentSettings(_env_file=None, RINGLENS_OPENAI_API_KEY="secret-do-not-show")
    assert "secret-do-not-show" not in repr(settings)
    with pytest.raises(ProviderError, match="RINGLENS_AGENT_MODEL"):
        OpenAIProvider(settings)
    # Never consult a developer's real .env or contact a provider from this unit test.
    monkeypatch.setattr("ringlens.cli.AgentSettings", lambda: AgentSettings(_env_file=None))
    result = CliRunner().invoke(app, ["agent", "run", "--message", "Explain refund ref_missing"])
    assert result.exit_code == 1 and "RINGLENS_AGENT_MODEL" in result.output


@pytest.mark.parametrize(
    "http_status,output_status", [(200, "completed"), (200, "incomplete"), (401, "completed")]
)
def test_official_sdk_native_calls_and_redacted_errors(monkeypatch, http_status, output_status):
    requests, options = [], []

    def transport(request):
        requests.append(json.loads(request.content))
        if http_status != 200:
            return httpx.Response(http_status, json={"error": {"message": "raw-secret"}})
        return httpx.Response(
            200,
            json={
                "id": "resp_mock",
                "object": "response",
                "created_at": 1,
                "model": "test-model",
                "status": output_status,
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "analyze_refund",
                        "arguments": '{"refund_id":"ref_test"}',
                        "status": "completed",
                    }
                ],
            },
        )

    client = openai.OpenAI(
        api_key="fake-secret",
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
        max_retries=0,
    )

    def factory(**kwargs):
        options.append(kwargs)
        return client

    monkeypatch.setattr(openai, "OpenAI", factory)
    settings = AgentSettings(
        _env_file=None, RINGLENS_OPENAI_API_KEY="fake-secret", agent_model="test-model"
    )
    provider = OpenAIProvider(settings)
    assert options[0]["timeout"] == 30 and options[0]["max_retries"] == 1
    try:
        if http_status != 200 or output_status != "completed":
            with pytest.raises(ProviderError) as error:
                provider.next_turn(
                    [{"role": "user", "content": "Explain"}], tool_schemas(), "assess"
                )
            assert "raw-secret" not in str(error.value) and "fake-secret" not in str(error.value)
        else:
            turn = provider.next_turn(
                [{"role": "user", "content": "Explain"}], tool_schemas(), "assess"
            )
            assert turn.calls[0]["name"] == "analyze_refund" and not turn.finished
        assert requests[0]["store"] is False
        assert requests[0]["parallel_tool_calls"] is False
        assert "untrusted DATA" in requests[0]["instructions"]
        assert all(t["strict"] for t in requests[0]["tools"])
    finally:
        provider.close()


def test_tool_schemas_and_write_boundary_without_database():
    schemas = tool_schemas()
    for tool in schemas:
        params = tool["parameters"]
        assert params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])
    tools = AgentTools(None)
    assert (
        tools.execute("apply_case_policy", {"assessment_reference": "anything"})["status"]
        == "ERROR"
    )
    assert (
        tools.execute("analyze_refund", {"refund_id": "ref_ok", "score": 100})["status"] == "ERROR"
    )
    assert tools.execute("record_review", {})["status"] == "ERROR"


def test_empty_project_key_allows_standard_environment_fallback(monkeypatch):
    monkeypatch.setenv("RINGLENS_OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-fallback")
    settings = AgentSettings(_env_file=None)
    assert settings.openai_api_key.get_secret_value() == "fake-fallback"


def fact(records, total_users=3, evidence_id="ev_test"):
    payload = {
        "anchor": "dev_shared",
        "records": records,
        "measurements": {"total_users": total_users},
    }
    identity = {"rule_id": "R1", "rule_version": "v1", "facts": payload}
    return EvidenceFact(
        fingerprint=sha256(canonical_json(identity).encode()).hexdigest(),
        rule_id="R1",
        rule_version="v1",
        evidence_id=evidence_id,
        source_ids=sorted(r["id"] for r in records),
        facts_json=canonical_json(payload),
    )


def test_fact_identity_and_narrow_scope():
    old = fact([{"id": "ord_one", "placed_at": 100}, {"id": "ord_two", "placed_at": 200}])
    same = fact(json.loads(old.facts_json)["records"], evidence_id="ev_other_assessment")
    assert old.fingerprint == same.fingerprint and _scope_covers(old, same)
    assert _scope_covers(old, fact([{"id": "ord_one", "placed_at": 100}]))
    assert not _scope_covers(old, fact([{"id": "ord_new", "placed_at": 300}]))
    assert not _scope_covers(old, fact(json.loads(old.facts_json)["records"], total_users=4))
    with pytest.raises(ValidationError, match="fingerprint"):
        EvidenceFact.model_validate({**old.model_dump(), "fingerprint": "0" * 64})


@pytest.mark.parametrize(
    "change",
    [
        {"explanation": " "},
        {"reviewer_id": " "},
        {"case_status": "CLOSED"},
        {"refund_id": "acct_wrong"},
        {"rule_ids": ["R99"]},
        {"as_of": True},
    ],
)
def test_human_review_input_validation(change):
    payload = {
        "refund_id": "ref_test",
        "reviewer_id": "human",
        "explanation": "Observed facts",
        "outcome": "BENIGN_EXPLANATION",
        **change,
    }
    with pytest.raises(ValidationError):
        ReviewRequest.model_validate(payload)
