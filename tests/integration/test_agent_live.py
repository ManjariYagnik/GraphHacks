"""Opt-in paid live-provider smoke; never confused with deterministic mocks."""

from pathlib import Path
from uuid import uuid4

import pytest

from ringlens.agent import get_agent_run, run_agent
from ringlens.agent_provider import AgentSettings, OpenAIProvider
from ringlens.config import Settings
from ringlens.graph_store import GraphStore
from ringlens.ingest import ingest_directory

pytestmark = [pytest.mark.integration, pytest.mark.live_agent]


def test_live_provider_read_only_smoke():
    settings = AgentSettings()
    if not settings.openai_api_key or not settings.agent_model:
        pytest.skip("Provider key/model not configured; no live call performed")
    name = "ringlens_test_live_agent_" + uuid4().hex
    with GraphStore(Settings(graph_name=name)) as store:
        assert not store.graph_exists(), "Refusing an existing graph"
        provider = OpenAIProvider(settings)
        try:
            ingest_directory(store, Path(__file__).resolve().parents[2] / "data/demo/base")
            result = run_agent(
                store,
                "Explain refund ref_2b42ec18e21a70bdfa15 using evidence and prior review context.",
                provider=provider,
                settings=settings,
            )
            assert result.status == "COMPLETE", result.errors
            assert "score=90" in result.response
            assert get_agent_run(store, result.run_id)["status"] == "COMPLETE"
            assert store.read("MATCH (i:Investigation) RETURN count(i)").result_set == [[0]]
        finally:
            provider.close()
            if name in store._db.list_graphs():
                store._db.select_graph(name).delete()
