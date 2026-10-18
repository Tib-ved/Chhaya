"""Claude standing in for Chhaya's text model and for the played resident, for the evaluation only.

On 18 October Vertex AI's text model was too congested to record the library of simulated
conversations, so they were recorded with Claude on both sides. These callables have exactly the
interfaces of app.ai_resident.make_agent_model and make_resident_model: the agent gets the real
call instruction and the real tools, and the real CallTools and after-call path decide the case.
Nothing here ships in the service; it runs only in backend/.venv-claude, where anthropic is installed."""
import json
import os
import threading
from pathlib import Path

from app import ai_resident
from app.ai_resident import OPENING_LINE, RATE_LIMIT_WAITS, AgentModel, ResidentModel, is_rate_limited
from app.call.guidance import TOOL_DECLARATIONS

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
MAX_TOKENS = 2048
TIMEOUT_SECONDS = 60
# Said for the other side when a model would otherwise be asked to answer itself (Claude takes no
# conversation that ends with its own turn).
SILENCE = "(Silence on the line.)"
# The words the run stops on: they match app.ai_resident.POLICY_REFUSALS.
REFUSED = "Request rejected in accordance with the model's usage policy (stop_reason refusal)"

TOOLS = [
    {"name": d["name"], "description": d["description"], "input_schema": d["parameters"]}
    for d in TOOL_DECLARATIONS
]


def _api_key() -> str | None:
    """The key from the environment, or else from backend/.env. Never printed."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    if ENV_FILE.is_file():
        from dotenv import dotenv_values

        return dotenv_values(ENV_FILE).get("ANTHROPIC_API_KEY") or None
    return None


def _lazy_client():
    """One client per model, made at its first call (tests never make one)."""
    clients: list = []
    lock = threading.Lock()

    def client():
        with lock:
            if not clients:
                import anthropic

                clients.append(anthropic.Anthropic(api_key=_api_key(), timeout=TIMEOUT_SECONDS))
        return clients[0]

    return client


def _rate_limited(error: BaseException) -> bool:
    if is_rate_limited(error) or getattr(error, "status_code", None) == 429:
        return True
    try:
        import anthropic
    except ImportError:
        return False
    return isinstance(error, anthropic.RateLimitError)


def _create(client, **request):
    """One request, with a rate limit waited out 20, 40 and 80 s as in app.ai_resident._generate.
    Any other error is let through; a refusal is raised so the run stops on it."""
    waits = list(RATE_LIMIT_WAITS)
    while True:
        try:
            message = client.messages.create(**request)
        except Exception as error:
            if not _rate_limited(error) or not waits:
                raise
            ai_resident.sleep(waits.pop(0))
            continue
        if getattr(message, "stop_reason", None) == "refusal":
            raise RuntimeError(REFUSED)
        return message


def _block_param(block) -> dict:
    """A content block of a reply, as it is sent back to the model."""
    kind = getattr(block, "type", None)
    if kind == "text":
        return {"type": "text", "text": block.text}
    if kind == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input or {})}
    return block.model_dump(exclude_none=True)  # such as a thinking block, passed back unchanged


def _merged(messages: list[dict]) -> list[dict]:
    """Consecutive messages of one side joined, a first message from the caller's side and a last
    one from the other side."""
    merged: list[dict] = []
    for message in messages:
        if not message["content"]:
            continue
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1] = {"role": message["role"], "content": [*merged[-1]["content"], *message["content"]]}
        else:
            merged.append(message)
    if not merged or merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": [{"type": "text", "text": OPENING_LINE}]})
    if merged[-1]["role"] != "user":
        merged.append({"role": "user", "content": [{"type": "text", "text": SILENCE}]})
    return merged


def agent_messages(conversation: list[dict]) -> list[dict]:
    """The agent's conversation as Claude's messages: its own replies as the assistant's (as the
    model returned them, when kept), the resident's lines and the tool results as the user's.
    A tool result is matched to its call by order, as CallTools runs the calls in order."""
    messages: list[dict] = []
    pending: list[str] = []  # the ids of the last reply's tool calls not yet answered
    for position, entry in enumerate(conversation):
        if entry["role"] == "agent":
            raw = entry.get("raw")
            if raw is not None:
                content = list(raw)
            else:
                content = [{"type": "text", "text": entry["text"]}] if entry.get("text") else []
                content += [{"type": "tool_use", "id": f"call_{position}_{n}", "name": c["name"],
                             "input": c.get("args") or {}} for n, c in enumerate(entry.get("tool_calls") or [])]
            pending = [block["id"] for block in content if block.get("type") == "tool_use"]
            messages.append({"role": "assistant", "content": content})
        elif entry["role"] == "tool":
            tool_use_id = pending.pop(0) if pending else f"call_{position}"
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": _result_text(entry["result"])}
            ]})
        elif entry.get("text"):
            messages.append({"role": "user", "content": [{"type": "text", "text": entry["text"]}]})
    return _merged(messages)


def _result_text(result) -> str:
    return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)


def read_agent_reply(message) -> dict:
    """What app.ai_resident expects back from the agent: its words, its tool calls and its own reply."""
    text, calls = [], []
    for block in getattr(message, "content", None) or []:
        kind = getattr(block, "type", None)
        if kind == "text":
            text.append(block.text)
        elif kind == "tool_use":
            calls.append({"name": block.name, "args": dict(block.input or {})})
    raw = [_block_param(block) for block in getattr(message, "content", None) or []]
    return {"text": "".join(text).strip(), "tool_calls": calls, "raw": raw}


def resident_messages(conversation: list[dict]) -> list[dict]:
    """The resident's conversation: Claude plays the resident, the agent's lines are the user's."""
    return _merged([
        {"role": "assistant" if entry["role"] == "resident" else "user",
         "content": [{"type": "text", "text": entry["text"]}]}
        for entry in conversation if entry.get("text")
    ])


def make_claude_agent_model(model: str = "claude-sonnet-5-5") -> AgentModel:
    """Chhaya's agent on Claude, with the tools of the live call."""
    client = _lazy_client()

    def agent_model(instruction: str, conversation: list[dict]) -> dict:
        message = _create(
            client(), model=model, max_tokens=MAX_TOKENS, system=instruction, tools=TOOLS,
            # No `thinking` setting: each model runs its own default, as the accepted values differ by
            # model (claude-haiku-5-5 refuses "between_tools").
            messages=agent_messages(conversation),
        )
        return read_agent_reply(message)

    return agent_model


def make_claude_resident_model(model: str = "claude-haiku-5-5") -> ResidentModel:
    """The resident, played by Claude from the persona instruction. It ends the call by writing
    app.ai_resident.HANG_UP, as the instruction says."""
    client = _lazy_client()

    def resident_model(instruction: str, conversation: list[dict]) -> str:
        message = _create(
            client(), model=model, max_tokens=MAX_TOKENS, system=instruction,
            messages=resident_messages(conversation),
        )
        return "".join(
            block.text for block in getattr(message, "content", None) or [] if getattr(block, "type", None) == "text"
        ).strip()

    return resident_model
