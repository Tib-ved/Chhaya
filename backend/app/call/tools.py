"""Handles the voice agent's tool calls. Records facts; never decides or lowers a tier."""
import logging
import threading
from datetime import datetime
from typing import Callable

from app.call.guidance import END_CALL_OUTCOMES
from app.cases import upsert_case
from app.db import Store
from app.triage import ALLOWED_VALUES, REQUIRED_QUESTIONS, SEVERITY

logger = logging.getLogger(__name__)

RED_NOW = {("feeling", "severe_symptoms"), ("help", "urgent")}
RESPONDENTS = ("self", "family", "other")
EMERGENCY_SAY = (
    "Tell them now to call 108 or to ask someone nearby to call, "
    "and that a health worker is being alerted."
)
# An emergency call cannot end until the agent has been heard to say the line.
EMERGENCY_LINE_FIRST = {"error": "say the emergency line first", "say": EMERGENCY_SAY}
CALL_NOT_FOUND = {"error": "call not found"}
NOT_HEARD = {
    "error": "no answer heard",
    "do": (
        "Nothing was recorded: the person has not spoken since the last answer was recorded. "
        "Ask this one question on its own, stop, and wait for their reply. Record it only then."
    ),
}
FOLLOW_UPS_NOT_ASKED = {
    "error": "follow-ups not asked",
    "do": (
        "Nothing was recorded: you have not yet asked every follow-up that is always asked for this "
        "question. Ask the remaining ones now, one at a time, waiting for the reply to each, and "
        "record the answer after the last reply."
    ),
}
EARLIER_QUESTION_NOT_FINISHED = (
    "Nothing was recorded: the question {question!r} is not finished. Go back to it: ask what is "
    "listed under ask_next, one at a time, waiting for the reply to each, and record {question!r} "
    "after the last reply; then ask this question again."
)
# A model that will not ask them cannot stall the call: after this many refusals of one
# question, its answer is accepted.
MOST_FOLLOW_UP_REFUSALS = 2
MAX_TIMELINE_EVENTS = 300
MAX_TRANSCRIPT_ENTRIES = 400
MAX_TRANSCRIPT_CHARS = 20000
TRUNCATED_ENTRY = {"role": "system", "text": "[transcript truncated]"}


class CallTools:
    def __init__(
        self,
        store: Store,
        call_id: str,
        clock: Callable[[], datetime],
        always_asked: dict[str, list[str]] | None = None,
        order: list[str] | None = None,
    ) -> None:
        """`always_asked` is, by question id, the follow-ups everyone is asked (see guidance).
        Without it nothing is held back for a follow-up. `order` is the question ids, the one
        every call opens with first; with it, another question also waits for the follow-ups of
        the opening one, and of any other question already under way. Otherwise any order goes."""
        self._store = store
        self._call_id = call_id
        self._clock = clock
        self._transcript: list[dict] = []
        self._transcript_chars = 0
        self._truncated = False
        self._copied: tuple[int, int] | None = None  # the transcript's size when it was last copied
        self._lock = threading.Lock()
        self.ended: str | None = None
        # Facts whose store write failed. They wait here and flush() writes them, so that a
        # store failure during the call never loses what the person said.
        self._kept_respondent: str | None = None
        self._kept_answers: dict[str, dict] = {}
        self._kept_emergency: dict | None = None
        self._kept_end_outcome = False
        self._written_answers: dict[str, str] = {}  # the last (so the worst) value this call stored
        # How many times the person has replied since the last answer was taken. One reply
        # supports one answer, so an answer the agent made up without hearing anything is refused;
        # and the least worrying answer to a question with follow-ups for everyone needs a reply
        # to each of them. A reply is what the person says until someone else's words arrive.
        self._replies = 0
        self._in_reply = False
        # Whether the latest reply still answers something: not once the agent has since
        # spoken a whole turn that the person said nothing during or after.
        self._fresh = False
        self._heard_in_turn = False
        # Whether the agent has spoken in its current turn, and whether it flagged an emergency
        # in a turn where it had said nothing: the call may not end until it has said the line.
        self._spoke_in_turn = False
        self._emergency_line_owed = False
        self._reply_start: dict | None = None  # how to take the reply in progress back (see drop_reply)
        self._always_asked = dict(always_asked or {})
        self._order = list(order or [])
        self._follow_up_refusals: dict[str, int] = {}
        # Replies since the last answer that are known to be about a later question, and how
        # many replies there were when that was last worked out.
        self._replies_elsewhere = 0
        self._replies_seen = 0
        self._let_go: set[str] = set()  # questions whose follow-ups the agent would not ask
        self._started: dict[str, None] = {}  # questions refused for their own follow-ups: under way
        # Worrying answers that were refused until the follow-ups are asked. Never lost: the
        # worse of this and what is recorded later is stored, and flush() stores it if nothing is.
        self._held_answers: dict[str, dict] = {}
        self._respondent: str | None = None  # as the agent last gave it, stored or not
        # What happened on the line and when, never what was said. Appended to from the event
        # loop only; the worker thread is handed copies.
        self._timeline: list[dict] = []

    def handle(self, name: str, args: dict) -> dict:
        """Run one tool call. Never raises, whatever the model sent."""
        with self._lock:
            try:
                handler = (
                    {
                        "set_respondent": self._set_respondent,
                        "record_answer": self._record_answer,
                        "flag_emergency": self._flag_emergency,
                        "end_call": self._end_call,
                    }.get(name)
                    if isinstance(name, str)
                    else None
                )
                if handler is None:
                    return {"error": f"unknown tool {str(name)[:40]!r}"}
                return handler(args if isinstance(args, dict) else {})
            except Exception:
                logger.exception("tool %r failed for call %s", str(name)[:40], self._call_id)
                if name == "flag_emergency":
                    return {"error": "internal error", "say": EMERGENCY_SAY}
                return {"error": "internal error"}

    def _open_case_now(self, call: dict) -> None:
        """Best effort: the fact is already stored, and the end-of-call recompute opens the case."""
        try:
            opened = upsert_case(self._store, call["round_id"], call["resident_id"], self._clock())
            if opened is None:
                logger.error("a red fact on call %s opened no case", self._call_id)
        except Exception:
            logger.exception("could not open a case during call %s", self._call_id)

    def _read(self) -> tuple[dict | None, bool]:
        """The call document (None if there is none), and whether the store could be read."""
        try:
            return self._store.get("calls", self._call_id), True
        except Exception:
            logger.exception("could not read call %s", self._call_id)
            return None, False

    def _write(self, kind: str, fields: dict) -> bool:
        """Write to the call document, retrying once at once. False if both attempts failed."""
        for attempt in (1, 2):
            try:
                self._store.update("calls", self._call_id, fields)
                return True
            except Exception:
                logger.exception(
                    "could not write the %s of call %s (attempt %d)", kind, self._call_id, attempt
                )
        return False

    def _keep(self, kind: str) -> None:
        logger.error(
            "the %s of call %s is kept in memory, to be written when the call ends", kind, self._call_id
        )

    def _earlier_value(self, call: dict | None, readable: bool, question: str) -> str | None:
        """The value already stored for the question. If the store cannot be read, the value
        this call has itself written: nothing else writes a call's answers."""
        if not readable:
            return self._written_answers.get(question)
        return (((call or {}).get("answers") or {}).get(question) or {}).get("value")

    @staticmethod
    def _is_worse(order: list[str], earlier, value: str) -> bool:
        return earlier in order and order.index(earlier) > order.index(value)

    def _write_answer(self, question: str, entry: dict) -> bool:
        if not self._write("answer", {f"answers.{question}": entry}):
            return False
        self._written_answers[question] = entry["value"]
        return True

    @staticmethod
    def _emergency_fields(call: dict | None, reason: str, at: datetime) -> dict:
        """The emergency to store: the first time is kept and the reasons are joined."""
        earlier = (call or {}).get("emergency") or {}
        if earlier.get("reason"):
            reason = f"{earlier['reason']}; {reason}"
        return {"emergency": {"flagged": True, "reason": reason[:300], "at": earlier.get("at") or at}}

    def _set_respondent(self, args: dict) -> dict:
        kind = args.get("type")
        if kind not in RESPONDENTS:
            return {"error": "type must be one of the allowed values", "allowed_values": list(RESPONDENTS)}
        call, readable = self._read()
        if readable and call is None:
            return dict(CALL_NOT_FOUND)
        self._respondent = kind
        # The reply that says who is speaking is not an answer to a question.
        self._forget_replies()
        if self._write("respondent", {"respondent": kind}):
            self._kept_respondent = None
        else:
            self._kept_respondent = kind
            self._keep("respondent")
        return {"result": "recorded"}

    def _record_answer(self, args: dict) -> dict:
        question = args.get("question_id")
        value = args.get("value")
        if not isinstance(question, str) or question not in ALLOWED_VALUES:
            return {"error": "unknown question_id", "allowed_question_ids": sorted(ALLOWED_VALUES)}
        order = SEVERITY[question]
        if not isinstance(value, str) or value not in order:
            return {"error": "value is not allowed for this question", "allowed_values": order}
        quote = args.get("quote")
        quote = quote if isinstance(quote, str) else ""
        # A red answer is never refused: this check may only ever keep a case from looking better.
        if (question, value) not in RED_NOW and not (self._replies and self._fresh):
            logger.warning("call %s: an answer to %r was refused, nobody had spoken", self._call_id, question)
            return dict(NOT_HEARD)
        recorded = {"result": "recorded"}
        # The most worrying answer is never held back: nothing may delay a red fact. A stranger
        # is asked no questions at all.
        if value != order[-1] and self._respondent != "other":
            refusal = self._follow_ups_owed(question, order, value, quote)
            if refusal is not None:
                refusals = self._follow_up_refusals.get(question, 0)
                if refusals < MOST_FOLLOW_UP_REFUSALS:
                    self._follow_up_refusals[question] = refusals + 1
                    logger.warning(
                        "call %s: an answer to %r was refused, follow-ups of %r not yet asked",
                        self._call_id, question, refusal.get("finish_first", question),
                    )
                    return refusal
                # The agent will not ask them: let this answer through, and stop holding later
                # questions back for the same follow-ups. For the timeline only: run_call takes
                # the note out before the agent is answered.
                self._let_go.update(self._unfinished_before(question))
                recorded["accepted_after_refusals"] = refusals
        held = self._held_answers.pop(question, None)
        if held and self._is_worse(order, held["value"], value):
            value, quote = held["value"], held["quote"]  # what they said first is not made better
        self._forget_replies()

        call, readable = self._read()
        if readable and call is None:
            return dict(CALL_NOT_FOUND)
        # The worst-value rule covers what is stored and what is kept for later alike.
        stored = self._earlier_value(call, readable, question)
        kept = (self._kept_answers.get(question) or {}).get("value")
        if self._is_worse(order, stored, value) or self._is_worse(order, kept, value):
            return {"result": "kept_earlier_answer"}
        entry = {"value": value, "quote": quote[:300], "at": self._clock()}
        if self._write_answer(question, entry):
            self._kept_answers.pop(question, None)  # superseded: this value is at least as bad
            if call is not None and (question, value) in RED_NOW:
                self._open_case_now(call)
        else:
            self._kept_answers[question] = entry
            self._keep("answer")
        # "recorded" either way: the model must not repeat a question because the store failed.
        return recorded

    def _forget_replies(self) -> None:
        """The replies so far have been used: the next fact needs a reply of its own."""
        self._replies, self._in_reply, self._fresh = 0, False, False
        self._replies_elsewhere, self._replies_seen = 0, 0
        self._reply_start = None

    def _answered(self, question: str) -> bool:
        return question in self._written_answers or question in self._kept_answers

    def _unfinished_before(self, question: str) -> list[str]:
        """The questions under way, with follow-ups for everyone and no answer yet: the opening
        question (how they feel, always asked first), and any other whose answer was already
        refused for its own follow-ups. The rest may come in any order: the conversation
        follows the person."""
        if question not in self._order:
            return []
        under_way = dict.fromkeys([self._order[0], *self._started])
        return [
            other
            for other in under_way
            if other != question
            and self._always_asked.get(other)
            and not self._answered(other)
            and other not in self._let_go
        ]

    def _follow_ups_owed(self, question: str, order: list[str], value: str, quote: str) -> dict | None:
        """The refusal for an answer that must wait for follow-ups everyone is asked, or None.

        It waits when its own question has such follow-ups without a reply each, or when a
        question under way has not been finished. The instruction alone was not enough: on real
        calls the agent recorded an answer after the first reply, or went on to the next question."""
        unfinished = self._unfinished_before(question)
        if unfinished:
            # The latest reply was to this later question, not to a follow-up.
            if self._replies > self._replies_seen:
                self._replies_elsewhere += 1
            self._replies_seen = self._replies
        owed = unfinished[0] if unfinished else question
        follow_ups = self._always_asked.get(owed) or []
        replies = self._replies - self._replies_elsewhere
        if not unfinished and replies > len(follow_ups):
            return None
        if not unfinished:
            self._started[question] = None
        if value != order[0]:
            held = self._held_answers.get(question)
            if not held or self._is_worse(order, value, held["value"]):
                self._held_answers[question] = {"value": value, "quote": quote[:300], "at": self._clock()}
        # The first reply answered the question itself; each one after it, a follow-up.
        refusal = {**FOLLOW_UPS_NOT_ASKED, "ask_next": follow_ups[max(replies - 1, 0):]}
        if unfinished:
            refusal.update(finish_first=owed, do=EARLIER_QUESTION_NOT_FINISHED.format(question=owed))
        return refusal

    def _flag_emergency(self, args: dict) -> dict:
        say = {"say": EMERGENCY_SAY}
        self._emergency_line_owed = not self._spoke_in_turn
        reason = args.get("reason")
        reason = reason.strip() if isinstance(reason, str) and reason.strip() else "no reason given"
        at = self._clock()
        kept = self._kept_emergency
        if kept:  # an earlier flag is still waiting to be written: one flag, both reasons
            reason, at = f"{kept['reason']}; {reason}", kept["at"]
        call, readable = self._read()
        if readable and call is None:
            return {**CALL_NOT_FOUND, **say}
        if not self._write("emergency flag", self._emergency_fields(call, reason, at)):
            self._kept_emergency = {"reason": reason[:300], "at": at}
            self._keep("emergency flag")
            return {"error": "could not record the emergency", **say}
        self._kept_emergency = None
        if call is not None:
            self._open_case_now(call)
        return {"result": "flagged", **say}

    def _end_call(self, args: dict) -> dict:
        outcome = args.get("outcome")
        if not isinstance(outcome, str) or outcome not in END_CALL_OUTCOMES:
            return {"error": "outcome is not allowed", "allowed_values": list(END_CALL_OUTCOMES)}
        if self._emergency_line_owed:
            return dict(EMERGENCY_LINE_FIRST)
        if self.ended is not None:
            return {"result": "ending"}  # the first valid outcome wins
        self.ended = outcome
        if not self._write("end outcome", {"end_outcome": outcome}):
            self._kept_end_outcome = True
            self._keep("end outcome")
        return {"result": "ending"}

    def end_for_the_agent(self, outcome: str) -> dict:
        """End the call for an agent that did not end it after the help button. The emergency
        line it owes is not waited for: nobody else will say it, and the call must not stay open."""
        with self._lock:
            self._emergency_line_owed = False
        return self.handle("end_call", {"outcome": outcome})

    def questions_done(self) -> bool:
        """Whether every question asked of every resident has an answer, stored or kept."""
        return all(q in self._written_answers or q in self._kept_answers for q in REQUIRED_QUESTIONS)

    def add_transcript(self, role: str, text: str) -> None:
        if not text or not text.strip():
            return
        if role == "agent":
            self.agent_spoke()
        if role == "resident":
            if not self._in_reply:
                last = self._transcript[-1] if self._transcript else None
                self._reply_start = {
                    "fresh": self._fresh,
                    "heard_in_turn": self._heard_in_turn,
                    "entries": len(self._transcript),
                    # the line it will be joined to, if the person was also the last to speak
                    "joined": len(last["text"]) if last and last["role"] == "resident" else None,
                    "truncated": self._truncated,
                }
                self._replies += 1
            self._fresh = self._heard_in_turn = True
        self._in_reply = role == "resident"
        if self._truncated:
            return
        joins = bool(self._transcript) and self._transcript[-1]["role"] == role
        too_many = not joins and len(self._transcript) >= MAX_TRANSCRIPT_ENTRIES
        too_long = self._transcript_chars + len(text) > MAX_TRANSCRIPT_CHARS
        if too_many or too_long:
            self._truncated = True
            self._transcript.append(dict(TRUNCATED_ENTRY))
            return
        self._transcript_chars += len(text)
        if joins:
            self._transcript[-1]["text"] += text
        else:
            self._transcript.append({"role": role, "text": text, "at": self._clock()})

    def drop_reply(self) -> None:
        """Take back the reply in progress: it was the agent's own voice coming back down the
        line. Its words leave the transcript and it no longer counts as a reply. Call it where
        add_transcript is called (on the event loop). Does nothing if no reply is in progress."""
        start = self._reply_start
        if not self._in_reply or start is None:
            return
        self._reply_start = None
        self._in_reply = False
        self._replies = max(self._replies - 1, 0)
        self._fresh, self._heard_in_turn = start["fresh"], start["heard_in_turn"]
        if start["truncated"] or self._truncated:
            return  # the transcript was already full: its words were never in it
        joined = start["joined"]
        if joined is not None and len(self._transcript) == start["entries"]:
            entry = self._transcript[-1]
            self._transcript_chars -= len(entry["text"]) - joined
            entry["text"] = entry["text"][:joined]
        elif joined is None and len(self._transcript) == start["entries"] + 1:
            self._transcript_chars -= len(self._transcript.pop()["text"])

    @property
    def lines(self) -> int:
        """How many transcript entries there are so far."""
        return len(self._transcript)

    def agent_turn_began(self) -> None:
        """The agent has started to speak a turn that the person will hear."""
        self._heard_in_turn = False
        self._spoke_in_turn = False

    def agent_spoke(self) -> None:
        """The agent's speech was heard in this turn (audio forwarded or text added). If it
        flagged an emergency before saying anything, the line has now been said."""
        self._spoke_in_turn = True
        self._emergency_line_owed = False

    def agent_turn_ended(self) -> None:
        """That turn is over. A reply heard before it began was a reply to something earlier:
        it no longer supports an answer (on a real call, "she is resting" was recorded as the
        answer to "how is she feeling", asked after it). What it said is behind it too: a
        flag_emergency in a later reply with nothing said (tool calls alone open no turn) still
        owes the 108 line."""
        self._spoke_in_turn = False
        if not self._heard_in_turn:
            self._fresh = False

    def transcript_if_changed(self) -> list[dict] | None:
        """A copy of the transcript if it has changed since the last copy, else None.

        Call it where add_transcript is called (on the event loop), so that the copy is taken
        between two additions and never during one."""
        size = (len(self._transcript), sum(len(entry["text"]) for entry in self._transcript))
        if size == self._copied or not self._transcript:
            return None
        self._copied = size
        return [dict(entry) for entry in self._transcript]

    def add_event(self, kind: str, **fields) -> None:
        """Note something that happened on the line: when, and what kind of thing.

        The fields are numbers, booleans and short fixed strings; never anything the person or
        the agent said. Only the last MAX_TIMELINE_EVENTS are kept. Never raises."""
        try:
            self._timeline.append({"at": self._clock(), "kind": kind, **fields})
            del self._timeline[:-MAX_TIMELINE_EVENTS]
        except Exception:
            logger.exception("could not note an event of call %s", self._call_id)

    def timeline(self) -> list[dict]:
        """A copy of the events so far. Call it where add_event is called (on the event loop)."""
        return [dict(event) for event in self._timeline]

    def write_transcript(self, entries: list[dict], timeline: list[dict] | None = None) -> None:
        """Store a copy made by transcript_if_changed, so a screen can follow the call, and with
        it a copy of the timeline, so a call that is cut off keeps what it had. Never
        raises: the complete transcript is written again by flush() when the call ends."""
        fields: dict = {"transcript": entries}
        if timeline is not None:
            fields["timeline"] = timeline
        try:
            self._store.update("calls", self._call_id, fields)
        except Exception:
            logger.exception("could not save the running transcript of call %s", self._call_id)

    def _write_kept(self) -> None:
        """Try once more to write every fact whose write failed during the call."""
        call, readable = self._read()
        if self._kept_respondent and self._write("respondent", {"respondent": self._kept_respondent}):
            self._kept_respondent = None
        for question, entry in list(self._kept_answers.items()):
            # A kept answer never replaces a worse one that is stored.
            stored = self._earlier_value(call, readable, question)
            if self._is_worse(SEVERITY[question], stored, entry["value"]) or self._write_answer(question, entry):
                del self._kept_answers[question]
        kept = self._kept_emergency
        if kept and self._write("emergency flag", self._emergency_fields(call, kept["reason"], kept["at"])):
            self._kept_emergency = None
        if self._kept_end_outcome and self._write("end outcome", {"end_outcome": self.ended}):
            self._kept_end_outcome = False
        lost = (
            ["respondent"] * bool(self._kept_respondent)
            + ["answer"] * len(self._kept_answers)
            + ["emergency flag"] * bool(self._kept_emergency)
            + ["end outcome"] * self._kept_end_outcome
        )
        if lost:
            logger.error(
                "call %s ended with facts that could not be written: %s", self._call_id, ", ".join(lost)
            )

    def flush(self) -> None:
        """Write the facts kept after a failed write, then the transcript and the timeline.
        Never raises.

        Must run before the call's outcome is recorded: the outcome is decided from, and the
        case is computed from, what the call document holds."""
        with self._lock:
            try:
                for question, entry in self._held_answers.items():
                    if not self._answered(question):
                        self._kept_answers[question] = entry
                self._held_answers = {}
                self._write_kept()
            except Exception:
                logger.exception("could not write the kept facts of call %s", self._call_id)
        try:
            self._store.update(
                "calls", self._call_id,
                {"transcript": [dict(entry) for entry in self._transcript], "timeline": self.timeline()},
            )
        except Exception:
            logger.exception("could not write the transcript for call %s", self._call_id)

    def final_status(self, reason: str) -> str:
        if reason == "open_failed":
            return "failed"
        return "completed" if self.ended else "dropped"
