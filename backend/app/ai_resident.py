"""One text conversation between Chhaya's agent and a resident played by the text model.

The agent gets the instruction of a real call, its tool calls are run by the real CallTools,
and the call ends through the real after-call path, so the case and its tier are decided as
for a phone call. Only the voice is missing: both sides are text model calls. Nothing here is
used by the live voice path."""
import logging
import time
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from app.call.deps import CallDeps
from app.call.guidance import TOOL_DECLARATIONS, opening_prompt
from app.call.live import ALREADY_SPOKEN, _tool_event
from app.call.session import _finalise, _prepare
from app.call.tools import CallTools
from app.db import Store
from app.dispatch import RecordingQueue

logger = logging.getLogger(__name__)

# (system instruction, conversation) -> {"text": str, "tool_calls": [{"name": str, "args": dict}]}.
# The conversation is a list of {"role": "agent", "text", "tool_calls"}, {"role": "resident",
# "text"} and {"role": "tool", "name", "args", "result"}.
AgentModel = Callable[[str, list[dict]], dict]
# (persona instruction, conversation) -> the resident's next line. The conversation holds the
# agent's and the resident's lines only, never the tool calls.
ResidentModel = Callable[[str, list[dict]], str]

HANG_UP = "[HANG UP]"
# A turn of the agent's is its reply and its replies to the results of its tool calls; a model
# that keeps calling tools without end is stopped after this many replies in one turn.
MAX_REPLIES_PER_TURN = 6
MODEL_TIMEOUT_MS = 30000
# What the model service says when it refuses a request on policy grounds.
POLICY_REFUSALS = ("rejected in accordance with", "PERMISSION_DENIED")
# What the model service says when its quota is used up, and how long to wait before each new try.
RATE_LIMITS = ("429", "RESOURCE_EXHAUSTED")
RATE_LIMIT_WAITS = (20, 40, 80)
sleep = time.sleep  # replaced in tests, so they do not wait
LANGUAGE_NAMES = {"hi": "Hindi", "mwr": "Marwari", "en": "English"}
OPENING_LINE = "(The phone is answered.)"


def is_policy_refusal(error: BaseException) -> bool:
    """Whether the model service refused the request on policy grounds. Asking again is no use."""
    return any(marker in str(error) for marker in POLICY_REFUSALS)


def is_rate_limited(error: BaseException) -> bool:
    """Whether the model service answered that its quota is used up. Asking again later may do."""
    return any(marker in str(error) for marker in RATE_LIMITS)


def persona_instruction(persona: dict) -> str:
    """What the resident model is told: who to play, and how."""
    language = persona.get("language", "hi")
    name = LANGUAGE_NAMES.get(language, language)
    facts = persona.get("facts") or {}
    if isinstance(facts, dict):
        listed = [f"- {topic}: {fact}" for topic, fact in facts.items()]
    else:
        listed = [f"- {fact}" for fact in facts]
    respondent = persona.get("respondent", "self")
    who = (
        "You are a family member of the older person being called, answering their phone."
        if respondent == "family"
        else "You are the older person being called, answering your own phone."
    )
    lines = [
        "You are playing a resident of a village in Rajasthan who gets a wellness call on a hot day "
        "from Chhaya, a heat-health helpline. Stay in character for the whole call; never say you "
        "are an AI or that this is a test.",
        who,
    ]
    if persona.get("situation"):
        lines.append(f"Your situation: {persona['situation']}")
    lines += [
        f"Speak only in {name} ({language}), as people speak it on the phone, in Latin letters.",
        "Answer in one or two short spoken sentences, as on a phone call. Answer what you were "
        "asked and nothing more.",
        "What is true about you is listed below. Tell each fact only when you are asked about it; "
        "never volunteer facts you were not asked about.",
        *listed,
        "If you are asked something not covered above, answer plainly and briefly in character.",
        "Write only your own words: no stage directions, no names before your line.",
    ]
    turns = persona.get("hangs_up_after")
    if turns:
        lines.append(
            f"After {turns} of your replies, end the call: say one short line and then write {HANG_UP} "
            "at the end of it."
        )
    else:
        # Left to itself the played resident hangs up after its thanks, before the agent has asked
        # everything, and a call cut short is amber by rule: the caller closes the call instead.
        lines.append(
            "Do not end the call yourself. Answer every question, even after you have said thank you; "
            "when the caller says goodbye, reply briefly and let them close the call."
        )
    return "\n".join(lines)


def _for_resident(conversation: list[dict]) -> list[dict]:
    """The conversation as the resident heard it: spoken lines only."""
    return [
        {"role": entry["role"], "text": entry["text"]}
        for entry in conversation
        if entry["role"] in ("agent", "resident") and entry.get("text")
    ]


def _read_result(store: Store, call_id: str) -> dict:
    call = store.get("calls", call_id) or {}
    case = None
    if call.get("round_id") and call.get("resident_id"):
        case = store.get("cases", f"{call['round_id']}_{call['resident_id']}")
    return {
        "status": call.get("status"),
        "transcript": call.get("transcript") or [],
        "answers": call.get("answers") or {},
        "respondent": call.get("respondent"),
        "emergency": call.get("emergency"),
        "tier": (case or {}).get("tier"),
        "timeline": call.get("timeline") or [],
    }


def _agent_turn(agent: AgentModel, instruction: str, tools: CallTools, conversation: list[dict]) -> None:
    """One turn of the agent's, fed to CallTools as live.run_call feeds it: the turn begins at
    the first thing the agent says (a turn of tool calls alone is no turn the person hears),
    each tool call is run and its result handed back, and the turn ends after its last reply."""
    spoke = False
    for _ in range(MAX_REPLIES_PER_TURN):
        reply = agent(instruction, conversation) or {}
        text = (reply.get("text") or "").strip()
        calls = [call for call in reply.get("tool_calls") or [] if isinstance(call, dict)]
        entry: dict = {"role": "agent", "text": text, "tool_calls": calls}
        if reply.get("raw") is not None:
            entry["raw"] = reply["raw"]  # the model's own message, sent back to it as it was
        if text or calls:
            conversation.append(entry)
        if text:
            if not spoke:
                spoke = True
                tools.agent_turn_began()
            tools.add_transcript("agent", text)
        for call in calls:
            name = call.get("name")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            result = tools.handle(name, dict(args))
            event = _tool_event(name, args, result)
            event["line"] = tools.lines  # the clock may not move between calls; the place does
            # As on the phone: a tool called after the agent has spoken is told to wait for the
            # reply, except on a refusal or an emergency, whose own instruction must stand.
            if spoke and isinstance(result, dict) and "error" not in result and name != "flag_emergency":
                result = {**result, "now": ALREADY_SPOKEN}
                event["after_speech"] = True
            tools.add_event("tool", **event)
            conversation.append({"role": "tool", "name": name, "args": args, "result": result})
        if not calls or tools.ended:
            break
    else:
        logger.warning("an agent turn was stopped after %d replies to tool results", MAX_REPLIES_PER_TURN)
    if spoke:
        tools.agent_turn_ended()
    tools.add_event("agent_turn", spoke=spoke)


def run_conversation(
    store: Store,
    call_id: str,
    persona: dict,
    agent: AgentModel,
    resident: ResidentModel,
    clock: Callable[[], datetime],
    settings,
    tz: ZoneInfo,
    max_turns: int = 30,
) -> dict:
    """Hold one conversation on a queued call and store its outcome as a call's.

    Raises CallRefused, before changing anything, for a call that cannot be conducted. Once the
    call is marked started, its outcome is recorded however the conversation ends."""
    # Everything taken from the persona is worked out first, so that a malformed persona raises
    # before the call is touched.
    playing = persona_instruction(persona)
    language = persona.get("language")
    deps = CallDeps(store, RecordingQueue(), settings, clock, tz, open_session=None)
    resident_doc, instruction, script = _prepare(deps, call_id)
    # ---- the call is marked started: from here the finally block always records the outcome
    tools = CallTools(store, call_id, clock, **script)
    conversation: list[dict] = []
    reason = "error"  # what any failure, or an exit before the loop is done, means
    failure: Exception | None = None
    try:
        # The live agent is sent the opening as its first message; here it ends the instruction.
        agent_instruction = f"{instruction}\n\n{opening_prompt(resident_doc)}"
        for _ in range(max_turns):
            _agent_turn(agent, agent_instruction, tools, conversation)
            if tools.ended:
                # Unlike the phone, where the call waits for the goodbye to be heard, an
                # end_call with nothing said ends the conversation here too (as completed).
                reason = "ended_by_agent"
                break
            line = resident(playing, _for_resident(conversation)) or ""
            hangs_up = HANG_UP in line
            line = line.replace(HANG_UP, "").strip()
            if line:
                tools.add_event("heard")
                tools.add_transcript("resident", line)
                conversation.append({"role": "resident", "text": line})
            # The running copy, as the live session keeps it while the call goes on.
            entries = tools.transcript_if_changed()
            if entries is not None:
                tools.write_transcript(entries, tools.timeline())
            if hangs_up:
                reason = "caller_hung_up"
                break
        else:
            reason = "timeout"  # out of turns
    except Exception as error:
        logger.exception("the conversation on call %s ended with an error", call_id)
        reason = "error"
        failure = error
    finally:
        _finalise(deps, call_id, tools, reason, language if isinstance(language, str) else resident_doc["language"])
    result = _read_result(store, call_id)
    if failure is not None:
        result["error"] = failure  # the caller may need to stop on it, as on a policy refusal
    return result


def _client(settings):
    from google import genai

    return genai.Client(
        vertexai=True, project=settings.gcp_project, location=settings.text_location,
        http_options={"timeout": MODEL_TIMEOUT_MS},
    )


def _generate(client, settings, contents: list[dict], config: dict):
    if settings.text_thinking.strip():
        config["thinking_config"] = {"thinking_level": settings.text_thinking.strip()}
    waits = list(RATE_LIMIT_WAITS)
    retried = False
    while True:
        try:
            return client.models.generate_content(model=settings.text_model, contents=contents, config=config)
        except Exception as error:
            if is_policy_refusal(error):
                raise  # a refusal is not asked twice
            if is_rate_limited(error):
                # the quota is shared: wait longer each time, then give up
                if not waits:
                    raise
                sleep(waits.pop(0))
            elif retried:
                raise
            else:
                retried = True  # a passing server error now and then: once more at once, then give up


def _merged(contents: list) -> list:
    """Consecutive messages of one side joined, and a first message from the caller's side.
    A message kept as the model returned it is passed on as it is."""
    merged: list = []
    for content in contents:
        if isinstance(content, dict) and not content["parts"]:
            continue
        last = merged[-1] if merged else None
        if isinstance(content, dict) and isinstance(last, dict) and last["role"] == content["role"]:
            merged[-1] = {"role": content["role"], "parts": [*last["parts"], *content["parts"]]}
        else:
            merged.append(content)
    if not merged or not isinstance(merged[0], dict) or merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "parts": [{"text": OPENING_LINE}]})
    return merged


def make_agent_model(settings) -> AgentModel:
    """Chhaya's agent on the text model, with the tools of the live call."""
    clients: list = []

    def agent_model(instruction: str, conversation: list[dict]) -> dict:
        if not clients:
            clients.append(_client(settings))
        contents: list = []
        for entry in conversation:
            if entry["role"] == "agent":
                raw = entry.get("raw")
                if raw is not None:  # keeps what the model needs back, such as its thought signatures
                    contents.append(raw)
                    continue
                parts = [{"text": entry["text"]}] if entry.get("text") else []
                parts += [{"function_call": {"name": c["name"], "args": c.get("args") or {}}} for c in entry["tool_calls"]]
                contents.append({"role": "model", "parts": parts})
            elif entry["role"] == "tool":
                contents.append({"role": "user", "parts": [
                    {"function_response": {"name": entry["name"], "response": entry["result"]}}
                ]})
            else:
                contents.append({"role": "user", "parts": [{"text": entry["text"]}]})
        config = {
            "system_instruction": instruction,
            "temperature": 0.4,
            "tools": [{"function_declarations": TOOL_DECLARATIONS}],
            "automatic_function_calling": {"disable": True},
        }
        response = _generate(clients[0], settings, _merged(contents), config)
        candidate = (response.candidates or [None])[0] if response is not None else None
        content = getattr(candidate, "content", None)
        text, calls = [], []
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "function_call", None):
                calls.append({"name": part.function_call.name, "args": dict(part.function_call.args or {})})
            elif getattr(part, "text", None) and not getattr(part, "thought", False):
                text.append(part.text)
        return {"text": "".join(text).strip(), "tool_calls": calls, "raw": content}

    return agent_model


def make_resident_model(settings) -> ResidentModel:
    """The resident, played by the text model from the persona instruction."""
    clients: list = []

    def resident_model(instruction: str, conversation: list[dict]) -> str:
        if not clients:
            clients.append(_client(settings))
        # The resident is the model; what the agent says reaches it as the other side's turns.
        contents = [
            {"role": "model" if entry["role"] == "resident" else "user", "parts": [{"text": entry["text"]}]}
            for entry in conversation
            if entry.get("text")
        ]
        config = {"system_instruction": instruction, "temperature": 0.8}
        response = _generate(clients[0], settings, _merged(contents), config)
        return (getattr(response, "text", None) or "").strip()

    return resident_model
