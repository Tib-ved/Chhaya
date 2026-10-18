from google.adk.tools.agent_tool import AgentTool

from app.ask.agent import build_agents
from app.ask.prompts import focus_line

READ_ONLY_PREFIXES = ("set", "update", "delete", "create", "approve", "reject", "mark", "assign", "send", "write")


def names(agent):
    return [getattr(t, "__name__", None) or t.name for t in agent.tools]


def test_root_has_every_tool_and_both_specialists():
    root = build_agents("gemini-3.8-flash")
    tool_names = names(root)
    for expected in ("list_areas", "get_area_overview", "list_alerts", "list_rounds", "list_cases", "get_case",
                     "top_risk_residents", "find_resident", "get_heat_status", "get_resident", "list_calls", "get_call",
                     "get_ward_staff", "show_chart", "show_table", "analyst", "call_summarizer"):
        assert expected in tool_names


def test_nothing_in_the_tree_can_write():
    root = build_agents("gemini-3.8-flash")
    agents = [root] + [t.agent for t in root.tools if isinstance(t, AgentTool)]
    for agent in agents:
        for name in names(agent):
            assert not name.startswith(READ_ONLY_PREFIXES), name


def test_specialists():
    root = build_agents("gemini-3.8-flash")
    specialists = {t.agent.name: t.agent for t in root.tools if isinstance(t, AgentTool)}
    assert names(specialists["call_summarizer"]) == ["get_transcript"]
    assert specialists["analyst"].output_schema and specialists["call_summarizer"].output_schema


def test_focus_line():
    assert "churu-ward-1" in focus_line({"state": "RJ", "district_id": "churu", "ward_id": "churu-ward-1"})
    assert "all areas" in focus_line({})


def test_the_runner_caches_the_instruction_and_tools():
    from datetime import datetime, timezone

    from app.ask.routes import build_runner
    from app.ask.sessions import StoreSessionService
    from app.config import Settings
    from app.db import MemoryStore

    settings = Settings(_env_file=None, gcp_project="chhaya-pilot", text_model="gemini-3.8-flash", ask_cache_seconds=600)
    runner = build_runner(settings, StoreSessionService(MemoryStore(), lambda: datetime.now(timezone.utc)))
    assert runner.context_cache_config is not None
    assert runner.context_cache_config.ttl_seconds == 600
