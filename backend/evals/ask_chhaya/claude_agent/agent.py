"""Ask Chhaya's agent tree with Claude standing in for Gemini, for the evaluation only.

On 18 October Vertex AI was too congested to run the golden set, so `python -m evals.ask_chhaya.run
--claude` points `adk eval` at this package instead of app/ask. It builds the same agents, tools and
topic gate as the app (app.ask.agent.build_agents), with Claude through the Anthropic API as the model.
Nothing here ships in the service; it runs only in backend/.venv-claude, where anthropic is installed.

    ANTHROPIC_API_KEY       from the environment, else backend/.env
    ASK_CLAUDE_MODEL        the agents' model (default claude-opus-5-5)
    ASK_CLAUDE_SMALL        the topic gate's model (default claude-haiku-5-5)
    ANTHROPIC_WORKSPACE_ID  needed only when the API key is not scoped to a workspace
"""
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
if str(BACKEND) not in sys.path:  # adk eval imports this package by its own folder's parent
    sys.path.insert(0, str(BACKEND))

MODEL = "claude-opus-5-5"
SMALL_MODEL = "claude-haiku-5-5"
GATE_SECONDS = 6.0  # a Claude call from a laptop is slower than Gemini from Cloud Run


def models() -> tuple[str, str]:
    return (os.environ.get("ASK_CLAUDE_MODEL") or MODEL, os.environ.get("ASK_CLAUDE_SMALL") or SMALL_MODEL)


def _api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env_file = BACKEND / ".env"
    if env_file.is_file():
        from dotenv import dotenv_values

        return dotenv_values(env_file).get("ANTHROPIC_API_KEY") or None
    return None


def _client():
    from anthropic import AsyncAnthropic

    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    headers = {"anthropic-workspace-id": workspace} if workspace else None
    return AsyncAnthropic(api_key=_api_key(), max_retries=1, default_headers=headers)


def _config(_temperature: float):
    # Current Claude models reject sampling settings; effort is the control. Low keeps answers quick.
    from google.adk.models.anthropic_llm import AnthropicGenerateContentConfig

    return AnthropicGenerateContentConfig(effort="low")


async def _ask_small(instruction: str, text: str, max_tokens: int) -> str:
    response = await _client().messages.create(
        model=models()[1], max_tokens=max_tokens, system=instruction,
        output_config={"effort": "low"}, messages=[{"role": "user", "content": text}],
    )
    if response.stop_reason == "refusal":
        raise ValueError("refused")
    return "".join(block.text for block in response.content if block.type == "text")


def register_judge() -> None:
    """Make a model named claude-* that ADK builds by name (the eval's judge, from --judge-model) call
    Claude through the Anthropic API with this module's client, instead of through Vertex AI.

    ADK's registry keeps one class per pattern and resolves a name by the first pattern that matches;
    claude-.* is ADK's own pattern for its Vertex Claude class, so registering it again replaces that
    class in place, and no earlier pattern matches a claude- name."""
    from google.adk.models.anthropic_llm import AnthropicLlm
    from google.adk.models.registry import LLMRegistry

    class DirectClaude(AnthropicLlm):
        def model_post_init(self, context) -> None:
            super().model_post_init(context)
            if self.client is None:
                self.client = _client()  # with the workspace header, which the SDK does not read itself

    LLMRegistry.register(DirectClaude)


def use_fixture() -> None:
    """The tools read the fixed evaluation dataset (evals/ask_chhaya/fixture.py), never a live store:
    app.ask.context builds it on first use when ASK_FIXTURE=1, which run.py also sets."""
    os.environ.setdefault("ASK_FIXTURE", "1")


def build_root():
    from google.adk.models.anthropic_llm import AnthropicLlm

    from app.ask.agent import build_agents
    from app.ask.guard import CLASSIFIER_INSTRUCTION, make_topic_gate, parse_label

    async def classify(conversation: str) -> str:
        return parse_label(await _ask_small(CLASSIFIER_INSTRUCTION, conversation, 1024))

    model = AnthropicLlm(model=models()[0], client=_client())
    return build_agents(model, make_topic_gate(classify, GATE_SECONDS), config=_config)


register_judge()  # adk eval loads this package before it grades, in the same process

_built: list = []


def __getattr__(name: str):
    """root_agent is built on first use (adk eval reads it), as app/ask/agent.py does."""
    if name == "root_agent":
        if not _built:
            use_fixture()
            _built.append(build_root())
        return _built[0]
    raise AttributeError(name)
