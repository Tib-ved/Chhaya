"""Record Chhaya's real Hindi greeting in a few voices, to choose one by ear. Run by a person:

    cd backend
    GCP_PROJECT=chhaya-pilot python -m scripts.voice_samples [Voice:woman|man ...]

Writes ../docs/voice-samples/<voice>.wav (24 kHz mono). Needs application-default credentials.
The default voice is not changed: set LIVE_VOICE, and LIVE_SPEAKS_AS to the voice's gender.
"""
import asyncio
import sys
import wave
from pathlib import Path

from app.call.guidance import build_instruction, opening_prompt
from app.call.live import open_live_session
from app.config import Settings

RESIDENT = {"id": "sample", "name": "Sunita Sharma", "age": 72, "language": "hi", "conditions": []}
# The candidates of 15 Oct: warm, friendly, gentle, and the voice used so far ("Firm").
VOICES = {"Sulafat": "woman", "Achird": "man", "Vindemiatrix": "woman", "Kore": "woman"}
OUT = Path(__file__).resolve().parents[2] / "docs" / "voice-samples"


async def first_turn(settings: Settings, instruction: str, text: str) -> bytes:
    """The audio of the model's first turn after `text`."""
    audio = bytearray()
    async with open_live_session(settings, instruction, language="hi") as session:
        await session.send_client_content(turns={"role": "user", "parts": [{"text": text}]}, turn_complete=True)
        async for message in session.receive():
            if message.data:
                audio += message.data
            if message.server_content and message.server_content.turn_complete:
                break
    return bytes(audio)


def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(24000)
        out.writeframes(pcm)


async def main() -> None:
    chosen = dict(arg.split(":") for arg in sys.argv[1:]) or VOICES
    for voice, speaks_as in chosen.items():
        settings = Settings().model_copy(update={"live_voice": voice, "live_speaks_as": speaks_as})
        instruction = build_instruction(RESIDENT, "heat", peak_temp_c=46, speaks_as=speaks_as)
        pcm = await first_turn(settings, instruction, opening_prompt(RESIDENT))
        write_wav(OUT / f"{voice}.wav", pcm)
        print(f"{voice} ({speaks_as}): {len(pcm) / 48000:.1f} s -> {OUT / (voice + '.wav')}")


if __name__ == "__main__":
    asyncio.run(main())
