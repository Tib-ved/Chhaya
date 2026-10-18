"""Ask Chhaya's conversations belong to one dataset: the one of the app that started them."""
from fastapi.testclient import TestClient

from app.ask import context
from app.ask.sessions import SESSIONS
from app.config import Settings
from app.dispatch import RecordingQueue
from app.router import build_apps
from evals.ask_chhaya.fixture import FIXTURE_NOW, build_store
from tests.test_ask_api import ALICE, StubRunner, frames, text


class ModeRunner(StubRunner):
    """Records the dataset the tools are told they serve, during the turn."""

    def __init__(self):
        super().__init__([text("ok")])
        self.modes = []

    async def run_async(self, **kwargs):
        self.modes.append(context.mode())
        async for event in super().run_async(**kwargs):
            yield event


def make():
    base = build_store()
    runner = ModeRunner()
    s = Settings(_env_file=None, internal_secret="s3cret")
    client = TestClient(build_apps(base, RecordingQueue(), s, clock=lambda: FIXTURE_NOW, ask_runner=runner))
    client.base, client.runner = base, runner
    return client


def start(client, mode):
    made = client.post(f"/api/ask/sessions?mode={mode}", json={"focus": {"district_id": "churu"}}, headers=ALICE)
    assert made.status_code == 201
    return made.json()["session_id"]


def say(client, sid, mode, words="How hot is Churu?"):
    return client.post(f"/api/ask/sessions/{sid}/messages?mode={mode}", json={"text": words}, headers=ALICE)


def test_a_demo_conversation_stores_its_mode():
    client = make()
    with client:
        demo, live = start(client, "demo"), start(client, "live")
    assert client.base.get(SESSIONS, demo)["state"]["mode"] == "demo"
    assert client.base.get(SESSIONS, live)["state"]["mode"] == "live"


def test_a_demo_conversation_sent_to_the_live_app_is_409_and_not_answered():
    client = make()
    with client:
        sid = start(client, "demo")
        refused = say(client, sid, "live")
        assert refused.status_code == 409 and "demo data" in refused.json()["detail"]
        assert refused.json()["reason"] == "other_mode"  # the drawer forgets the conversation on this
        assert client.runner.seen == []
        assert say(client, sid, "demo").status_code == 200
    assert client.runner.modes == ["demo"]


def test_a_conversation_without_a_mode_is_live():
    client = make()
    with client:
        sid = start(client, "live")
        client.base.update(SESSIONS, sid, {"state": {"focus": {}}})  # made before modes were stored
        assert say(client, sid, "demo").status_code == 409
        assert say(client, sid, "live").status_code == 200
        listed = client.get("/api/ask/sessions", headers=ALICE).json()
    assert [s["session_id"] for s in listed] == [sid]


def test_history_lists_only_the_conversations_of_the_mode():
    client = make()
    with client:
        demo, live = start(client, "demo"), start(client, "live")
        say(client, demo, "demo"), say(client, live, "live")
        in_demo = [s["session_id"] for s in client.get("/api/ask/sessions?mode=demo", headers=ALICE).json()]
        in_live = [s["session_id"] for s in client.get("/api/ask/sessions", headers=ALICE).json()]
    assert in_demo == [demo] and in_live == [live]


def test_the_tools_read_the_mode_from_the_conversation():
    client = make()
    with client:
        say(client, start(client, "live"), "live")
        body = say(client, start(client, "demo"), "demo").text
    assert client.runner.modes == ["live", "demo"]
    assert frames(body)[-1][0] == "done"


def test_a_tool_gives_the_rule_of_the_mode_it_is_told():
    from app.ask import tools
    from app.datasets import DatasetStore
    from tests.test_ask_tools import run

    base = build_store()
    with context.use(DatasetStore(base, "demo"), lambda: FIXTURE_NOW, mode="demo"):
        assert context.mode() == "demo" and "thresholds_c" in run(tools.get_heat_status(""))
    with context.use(DatasetStore(base, "live"), lambda: FIXTURE_NOW, mode="live"):
        assert context.mode() == "live" and "thresholds_c" not in run(tools.get_heat_status(""))
