"""One real Gemini Live session with spoken input. Run by a person:

    cd backend
    GCP_PROJECT=chhaya-pilot python -m scripts.live_smoke [path-to-a-wav-of-someone-speaking]

On Windows also set PYTHONIOENCODING=utf-8, or printing the Hindi transcript fails.

Needs application-default credentials. With no WAV it sends two seconds of silence, which
still checks that the session opens with our config and that the agent greets first.
"""
import asyncio
import sys
import time
import warnings
import wave

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import audioop

from app.call.guidance import build_instruction, opening_prompt
from app.call.live import open_live_session, run_call
from app.config import Settings

RESIDENT = {"id": "smoke", "name": "Sunita Sharma", "age": 72, "language": "hi", "conditions": []}


class PrintingTools:
    def __init__(self):
        self.ended = None
        self.heard_resident = False

    def handle(self, name, args):
        print("TOOL:", name, args)
        if name == "end_call":
            self.ended = args.get("outcome")
            return {"result": "ending"}
        return {"result": "recorded"}

    def add_event(self, kind, **fields):
        print("EVENT:", kind, fields)

    def drop_reply(self):
        print("(the last reply was the agent's own echo)")

    def agent_turn_began(self):
        pass

    def agent_turn_ended(self):
        pass

    def agent_spoke(self):
        pass

    def questions_done(self):
        return False

    def add_transcript(self, role, text):
        if role == "resident":
            self.heard_resident = True
        print(f"{role}: {text}")


def load_pcm_16k(path: str) -> bytes:
    with wave.open(path, "rb") as source:
        frames = source.readframes(source.getnframes())
        if source.getnchannels() == 2:
            frames = audioop.tomono(frames, source.getsampwidth(), 0.5, 0.5)
        if source.getsampwidth() != 2:
            frames = audioop.lin2lin(frames, source.getsampwidth(), 2)
        if source.getframerate() != 16000:
            frames, _ = audioop.ratecv(frames, 2, 1, source.getframerate(), 16000, None)
    return frames


async def main() -> None:
    settings = Settings()
    speech = load_pcm_16k(sys.argv[1]) if len(sys.argv) > 1 else b"\x00" * 64000
    audio_in: asyncio.Queue = asyncio.Queue()
    received = 0
    started = time.monotonic()
    first_audio_at = None
    last_audio_at = None
    wav_ended = [None]
    after_wav_audio = [None]

    async def feed() -> None:
        # A real microphone streams continuously, silence included; the service detects the end
        # of a turn from trailing silence, so never let the stream simply stop.
        silence = b"\x00" * 640
        waited = 0.0  # wait for the greeting: first audio seen, then 1.5 s of quiet
        while waited < 30 and (last_audio_at is None or time.monotonic() - last_audio_at < 1.5):
            audio_in.put_nowait(silence)
            await asyncio.sleep(0.02)
            waited += 0.02
        for start in range(0, len(speech), 640):  # 20 ms chunks, paced like a live caller
            audio_in.put_nowait(speech[start : start + 640])
            await asyncio.sleep(0.02)
        wav_ended[0] = time.monotonic()
        for _ in range(400):  # 8 s of trailing silence
            audio_in.put_nowait(silence)
            await asyncio.sleep(0.02)
        audio_in.put_nowait(None)

    async def send_audio(pcm: bytes) -> None:
        nonlocal received, first_audio_at, last_audio_at
        last_audio_at = time.monotonic()
        if first_audio_at is None:
            first_audio_at = time.monotonic() - started
        received += len(pcm)
        if wav_ended[0] and after_wav_audio[0] is None:
            after_wav_audio[0] = time.monotonic() - wav_ended[0]

    async def clear_audio() -> None:
        print("(interrupted)")

    tools = PrintingTools()
    instruction = build_instruction(RESIDENT, "heat", peak_temp_c=46, speaks_as=settings.live_speaks_as)
    print("model:", settings.live_model)
    async with open_live_session(settings, instruction, language=RESIDENT["language"]) as session:
        started = time.monotonic()
        feeder = asyncio.create_task(feed())
        reason = await run_call(
            session, tools, audio_in, send_audio, clear_audio, opening_prompt(RESIDENT), 60,
            quiet_seconds=settings.live_quiet_seconds,
            quiet_limit=settings.live_quiet_limit,
            greeting_guard_seconds=settings.live_greeting_guard_seconds,
            closing_silence_seconds=settings.live_closing_silence_seconds,
            mute_repeats=settings.live_mute_repeats,
        )
        feeder.cancel()
        await asyncio.gather(feeder, return_exceptions=True)
    seconds = "none" if first_audio_at is None else f"{first_audio_at:.1f}s"
    gap = after_wav_audio[0]
    print(
        f"summary: resident text arrived: {tools.heard_resident} | model spoke after WAV: "
        f"{gap is not None} | seconds from WAV end to that audio: "
        f"{'n/a' if gap is None else f'{gap:.1f}'}"
    )
    print("ended:", reason, "| model audio bytes:", received, "| first model audio after:", seconds)


if __name__ == "__main__":
    asyncio.run(main())
