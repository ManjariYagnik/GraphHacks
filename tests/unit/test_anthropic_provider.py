"""Official Claude SDK over fake HTTP; never uses developer keys or live credits."""

import json

import anthropic
import httpx
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from ringlens.agent_provider import AgentSettings, AnthropicProvider, ProviderError, create_provider
from ringlens.agent_tools import tool_schemas
from ringlens.cli import app


def settings(**kwargs):
    return AgentSettings(
        _env_file=None,
        agent_provider="anthropic",
        RINGLENS_ANTHROPIC_API_KEY="fake-claude-secret",
        agent_model="claude-test-model",
        **kwargs,
    )


def message(content, stop_reason="tool_use"):
    return {
        "id": "msg_mock",
        "type": "message",
        "role": "assistant",
        "model": "claude-test-model",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def install_transport(monkeypatch, handler):
    options = []
    client = anthropic.Anthropic(
        api_key="fake-claude-secret",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    def factory(**kwargs):
        options.append(kwargs)
        return client

    monkeypatch.setattr(anthropic, "Anthropic", factory)
    return options


def test_claude_native_tools_round_trip_schema_and_no_openai(monkeypatch):
    requests = []

    def handler(request):
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        data = json.loads(request.content)
        requests.append(data)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=message(
                    [
                        {"type": "text", "text": "I will inspect the graph."},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "analyze_refund",
                            "input": {"refund_id": "ref_test", "as_of": None, "window_days": 30},
                        },
                    ]
                ),
            )
        return httpx.Response(200, json=message([{"type": "text", "text": "Done"}], "end_turn"))

    options = install_transport(monkeypatch, handler)
    provider = create_provider(settings())
    assert isinstance(provider, AnthropicProvider)
    assert options[0]["timeout"] == 30 and options[0]["max_retries"] == 1
    history = [{"role": "user", "content": "Explain refund ref_test"}]
    try:
        turn = provider.next_turn(history, tool_schemas(), "assess")
        assert turn.calls[0]["call_id"] == "toolu_1" and not turn.finished
        assert json.loads(turn.calls[0]["arguments"])["refund_id"] == "ref_test"
        history.extend(turn.items)
        history.append(
            {
                "type": "function_call_output",
                "call_id": "toolu_1",
                "output": json.dumps({"status": "OK", "data": {"score": 35}}),
            }
        )
        assert provider.next_turn(history, tool_schemas(), "assess").finished
        sent = requests[1]["messages"]
        assert sent[1]["content"][0]["text"] == "I will inspect the graph."
        assert sent[1]["content"][1]["id"] == "toolu_1"
        assert sent[2]["role"] == "user"
        assert sent[2]["content"][0]["tool_use_id"] == "toolu_1"
        assert sent[2]["content"][0]["is_error"] is False
        assert requests[0]["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
        assert "untrusted DATA" in requests[0]["system"]
        assert all(t["strict"] for t in requests[0]["tools"])
        schema = requests[0]["tools"][0]["input_schema"]
        assert schema["additionalProperties"] is False
        # Unsupported Anthropic grammar constraints are described, enforced in AgentTools.
        integer = schema["properties"]["window_days"]["anyOf"][0]
        assert "minimum" not in integer and "maximum" not in integer
        assert "minimum" in integer["description"]
        assert "responses" not in str(requests)
    finally:
        provider.close()


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal", "pause_turn", "tool_use"])
def test_incomplete_and_missing_tool_blocks_fail_explicitly(monkeypatch, stop_reason):
    install_transport(monkeypatch, lambda req: httpx.Response(200, json=message([], stop_reason)))
    provider = AnthropicProvider(settings())
    try:
        with pytest.raises(ProviderError, match="incomplete/refused"):
            provider.next_turn([{"role": "user", "content": "Explain"}], tool_schemas(), "assess")
    finally:
        provider.close()


@pytest.mark.parametrize("status", [401, 429, 500])
def test_claude_errors_are_sanitized_and_never_fall_back(monkeypatch, status):
    install_transport(
        monkeypatch,
        lambda req: httpx.Response(
            status,
            json={
                "type": "error",
                "error": {"type": "authentication_error", "message": "raw-secret"},
            },
        ),
    )
    monkeypatch.setattr(
        "ringlens.agent_provider.OpenAIProvider", lambda *_: pytest.fail("No fallback")
    )
    provider = create_provider(settings())
    try:
        with pytest.raises(ProviderError) as error:
            provider.next_turn([{"role": "user", "content": "Explain"}], tool_schemas(), "assess")
        assert "raw-secret" not in str(error.value) and "fake-claude-secret" not in str(error.value)
        assert "No fallback" in str(error.value)
    finally:
        provider.close()


def test_tool_errors_and_multiple_results_share_one_user_message():
    history = [
        {"role": "user", "content": "Explain"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "first", "name": "get_case", "input": {}}],
        },
        {
            "type": "function_call_output",
            "call_id": "first",
            "output": '{"status":"ERROR","errors":["missing case"]}',
        },
        {"type": "function_call_output", "call_id": "second", "output": '{"status":"INCOMPLETE"}'},
    ]
    converted = AnthropicProvider._messages(history)
    assert len(converted) == 3 and len(converted[-1]["content"]) == 2
    assert all(block["is_error"] for block in converted[-1]["content"])
    assert len(history) == 4  # Do not mutate the runner's audit/continuation history.


def test_claude_missing_configuration_secret_hiding_and_fallback_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RINGLENS_ANTHROPIC_API_KEY", raising=False)
    empty = AgentSettings(_env_file=None, agent_provider="anthropic", agent_model="claude-test")
    with pytest.raises(ProviderError, match="RINGLENS_ANTHROPIC_API_KEY"):
        create_provider(empty)
    assert "fake-claude-secret" not in repr(settings())
    monkeypatch.setenv("RINGLENS_ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-standard-key")
    assert AgentSettings(_env_file=None).anthropic_api_key.get_secret_value() == "fake-standard-key"
    with pytest.raises(ValidationError):
        AgentSettings(_env_file=None, agent_provider="untrusted_provider")
    # Missing credentials fail before database work; no unit test makes a live call.
    monkeypatch.setattr("ringlens.cli.AgentSettings", lambda: empty)
    result = CliRunner().invoke(app, ["agent", "run", "--message", "Explain"])
    assert result.exit_code == 1 and "RINGLENS_ANTHROPIC_API_KEY" in result.output
