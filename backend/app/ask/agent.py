"""Ask Chhaya's agents: the root answers and draws, two specialists work as its tools.

`adk eval app/ask …` loads `root_agent` from here; the app builds its own with build_agents.
"""
import os

from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.adk.tools.agent_tool import AgentTool
from google.genai import types
from pydantic import BaseModel, Field

from app.ask import context, prompts, timing
from app.ask.tools import ANALYST_TOOLS, ROOT_TOOLS, SUMMARIZER_TOOLS
from app.ask.visuals import show_chart, show_table

APP_NAME = "ask_chhaya"


class Finding(BaseModel):
    label: str
    value: float


class AnalystSeries(BaseModel):
    name: str
    points: list[Finding]


class AnalystOut(BaseModel):
    findings: list[str] = Field(description="Short sentences with exact numbers.")
    series: list[AnalystSeries] = Field(default_factory=list, description="Series ready to chart, if useful.")


class CallSummary(BaseModel):
    call_id: str
    summary: str
    wellbeing_flags: list[str] = Field(default_factory=list)
    answers_highlights: list[str] = Field(default_factory=list)
    follow_up_suggestions: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list, description="Exact words from the transcript.")


def _root_instruction(ctx) -> str:
    return prompts.ROOT.format(today=context.now().date().isoformat(), focus=prompts.focus_line(ctx.state.get("focus")))


MODEL_TIMEOUT_MS = 25_000  # one model request; ADK sets none, and on 14 Oct a first request now and then hung for over 90 s


def _config(temperature: float = 0.2) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        temperature=temperature,
        http_options=types.HttpOptions(timeout=MODEL_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=2)),
    )


def _planner(thinking: str) -> BuiltInPlanner | None:
    return BuiltInPlanner(thinking_config=types.ThinkingConfig(thinking_level=thinking)) if thinking else None


def build_agents(model, gate=None, thinking: str = "low", config=None) -> LlmAgent:
    """model: a Gemini model name, or an ADK model object. config: a function from temperature to the
    content config, for a model that takes other settings; Gemini's thinking planner is then left out."""
    make_config = config or _config
    watch = dict(before_model_callback=timing.before_model, after_model_callback=timing.after_model,
                 before_tool_callback=timing.before_tool, after_tool_callback=timing.after_tool)
    planner = (lambda: None) if config else (lambda: _planner(thinking))
    analyst = LlmAgent(
        name="analyst", model=model, description="Counts, groups, compares and trends Chhaya's cases, risk and calls.",
        instruction=prompts.ANALYST, tools=list(ANALYST_TOOLS), output_schema=AnalystOut,
        generate_content_config=make_config(0), planner=planner(), **watch,
    )
    summarizer = LlmAgent(
        name="call_summarizer", model=model, description="Summarises one finished wellness call from its transcript.",
        instruction=prompts.SUMMARIZER, tools=list(SUMMARIZER_TOOLS), output_schema=CallSummary,
        generate_content_config=make_config(0.1), planner=planner(), **watch,
    )
    return LlmAgent(
        name="chhaya", model=model, description="Ask Chhaya, the officer's analytics assistant.",
        instruction=_root_instruction,
        tools=[*ROOT_TOOLS, show_chart, show_table, AgentTool(analyst), AgentTool(summarizer)],
        generate_content_config=make_config(0.2), planner=planner(),
        **{**watch, "before_model_callback": [gate, timing.before_model] if gate else timing.before_model},
    )


def build_root(settings) -> LlmAgent:
    """The agent tree as the app and adk eval run it: Gemini on Vertex AI."""
    from app.ask.guard import make_classifier, make_topic_gate

    configure_vertex(settings)
    classify = make_classifier(settings)
    gate = make_topic_gate(classify, settings.ask_gate_seconds) if classify else None
    return build_agents(ask_model(settings), gate, settings.ask_thinking.strip())


def _default_root() -> LlmAgent:
    """For adk eval and adk web: settings from the environment."""
    from app.config import get_settings

    return build_root(get_settings())


def ask_model(settings) -> str:
    return settings.ask_model.strip() or settings.text_model.strip() or "gemini-3.8-flash"


def configure_vertex(settings) -> None:
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    if settings.gcp_project:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.gcp_project)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.text_location or "global")


_built: list[LlmAgent] = []


def __getattr__(name: str):
    """root_agent is built on first use (adk eval reads it), so importing this module never touches settings."""
    if name == "root_agent":
        if not _built:
            _built.append(_default_root())
        return _built[0]
    raise AttributeError(name)
