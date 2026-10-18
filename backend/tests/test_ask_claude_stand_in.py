"""The Ask Chhaya Claude stand-in (evals/ask_chhaya/claude_agent), which lives only in the eval harness.
Runs in backend/.venv-claude, where anthropic is installed; skipped elsewhere. Never calls a model."""
import pytest

pytest.importorskip("anthropic")  # installed only in .venv-claude

from google.adk.models import registry  # noqa: E402
from google.adk.models.anthropic_llm import AnthropicLlm, Claude  # noqa: E402
from google.adk.models.registry import LLMRegistry  # noqa: E402

from evals.ask_chhaya.claude_agent import agent as claude_agent  # noqa: E402


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("ASK_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("ASK_CLAUDE_SMALL", raising=False)


def test_builds_the_app_agents_on_claude(key):
    root = claude_agent.build_root()
    assert type(root.model).__name__ == "AnthropicLlm" and root.model.model == "claude-opus-5-5"
    assert root.name == "chhaya" and root.planner is None
    assert root.generate_content_config.temperature is None and root.generate_content_config.effort == "low"
    assert isinstance(root.before_model_callback, list) and len(root.before_model_callback) == 2  # the topic gate
    assert claude_agent.models() == ("claude-opus-5-5", "claude-haiku-5-5")


def test_reads_the_fixed_dataset(monkeypatch):
    import os

    from app.ask import context

    monkeypatch.delenv("ASK_FIXTURE", raising=False)
    monkeypatch.setattr(context, "_store", [])
    monkeypatch.setattr(context, "_clock", [])
    claude_agent.use_fixture()
    assert os.environ.get("ASK_FIXTURE") == "1"
    from evals.ask_chhaya.fixture import FIXTURE_NOW

    assert context.get_store() is not None and context.now() == FIXTURE_NOW


def test_a_claude_judge_named_by_adk_calls_the_anthropic_api(monkeypatch, key):
    # The registry is the process's: the test's registration is undone when it ends.
    monkeypatch.setattr(registry, "_llm_registry_dict", dict(registry._llm_registry_dict))
    LLMRegistry.resolve.cache_clear()
    claude_agent.register_judge()
    judge = LLMRegistry.new_llm("claude-opus-5-5")
    assert isinstance(judge, AnthropicLlm) and not isinstance(judge, Claude)  # Claude is the Vertex class
    assert judge.model == "claude-opus-5-5"
    assert type(judge.client).__name__ == "AsyncAnthropic"
    assert type(LLMRegistry.new_llm("gemini-3.8-flash")).__name__ == "Gemini"  # Gemini is untouched
    LLMRegistry.resolve.cache_clear()
