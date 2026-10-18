import asyncio

from app.ask import context
from app.db import MemoryStore


def test_each_request_reads_its_own_store():
    a, b = MemoryStore(), MemoryStore()

    async def read(store):
        with context.use(store, lambda: None):
            await asyncio.sleep(0)
            return context.get_store()

    async def both():
        return await asyncio.gather(read(a), read(b))

    first, second = asyncio.run(both())
    assert first is a and second is b


def test_a_demo_question_reads_the_demo_dataset_and_a_live_one_the_live():
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.dispatch import RecordingQueue
    from app.router import build_apps

    seen = []

    class Runner:
        async def run_async(self, **kwargs):
            seen.append(context.get_store().dataset)
            return
            yield

    settings = Settings(_env_file=None, internal_secret="s3cret")
    c = TestClient(build_apps(MemoryStore(), RecordingQueue(), settings, ask_runner=Runner()))
    team = {"X-Internal-Secret": "s3cret"}
    with c:
        for query in ("?mode=demo", "", "?mode=demo"):
            sid = c.post("/api/ask/sessions" + query, json={}, headers=team).json()["session_id"]
            c.post(f"/api/ask/sessions/{sid}/messages{query}", json={"text": "how many red cases?"}, headers=team)
    assert seen == ["demo", "live", "demo"]
