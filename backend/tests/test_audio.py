import base64
import math
import struct

from app.call.audio import GEMINI_INPUT_MIME, TWILIO_FRAME_BYTES, TwilioAudio

ULAW_SILENCE = b"\xff"


def pcm_tone(rate: int, milliseconds: int, hertz: int = 440) -> bytes:
    count = rate * milliseconds // 1000
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * hertz * n / rate))) for n in range(count)
    )


def test_constants():
    assert TWILIO_FRAME_BYTES == 160
    assert GEMINI_INPUT_MIME == "audio/pcm;rate=16000"


def test_one_twilio_frame_becomes_20ms_of_16k_pcm():
    audio = TwilioAudio()
    payload = base64.b64encode(ULAW_SILENCE * 160).decode()
    pcm = audio.to_gemini(payload)
    assert abs(len(pcm) - 640) <= 4  # 320 samples of 2 bytes, give or take a sample
    assert len(pcm) % 2 == 0


def test_caller_audio_stays_continuous_across_frames():
    audio = TwilioAudio()
    payload = base64.b64encode(ULAW_SILENCE * 160).decode()
    total = sum(len(audio.to_gemini(payload)) for _ in range(50))  # one second
    assert abs(total - 32000) <= 8


def test_model_audio_becomes_whole_160_byte_frames():
    audio = TwilioAudio()
    frames = audio.to_twilio(pcm_tone(24000, 100))  # 100 ms is five 20 ms frames
    assert 4 <= len(frames) <= 5
    for frame in frames:
        assert len(base64.b64decode(frame)) == 160


def test_remainder_is_held_until_enough_arrives():
    audio = TwilioAudio()
    first = audio.to_twilio(pcm_tone(24000, 10))  # half a frame
    assert first == []
    second = audio.to_twilio(pcm_tone(24000, 15))  # 25 ms in total: one frame and a bit
    assert len(second) == 1


def test_flush_pads_the_last_partial_frame_with_silence():
    audio = TwilioAudio()
    assert audio.to_twilio(pcm_tone(24000, 10)) == []
    flushed = audio.flush()
    assert len(flushed) == 1
    raw = base64.b64decode(flushed[0])
    assert len(raw) == 160
    assert raw.endswith(ULAW_SILENCE * 40)
    assert audio.flush() == []


def test_reset_output_drops_held_audio():
    audio = TwilioAudio()
    audio.to_twilio(pcm_tone(24000, 10))
    audio.reset_output()
    assert audio.flush() == []


def test_a_tone_survives_the_round_trip_as_sound_not_silence():
    audio = TwilioAudio()
    frames = audio.to_twilio(pcm_tone(24000, 200))
    raw = b"".join(base64.b64decode(frame) for frame in frames)
    assert len(set(raw)) > 20  # many distinct mu-law values, so not silence or a constant


def test_odd_length_and_empty_input_do_not_raise():
    audio = TwilioAudio()
    assert audio.to_twilio(b"") == []
    audio.to_twilio(pcm_tone(24000, 30) + b"\x01")  # a stray byte must not break the stream
    assert audio.to_gemini("") == b""


def test_invalid_base64_payload_gives_empty_bytes():
    audio = TwilioAudio()
    assert audio.to_gemini("not base64!!") == b""
    assert audio.to_gemini(None) == b""


def test_model_audio_is_continuous_across_calls():
    second = pcm_tone(24000, 1000)
    chunked = TwilioAudio()
    pieces = [second[i : i + 960] for i in range(0, len(second), 960)]  # fifty 20 ms pieces
    assert len(pieces) == 50
    count = sum(len(chunked.to_twilio(piece)) for piece in pieces) + len(chunked.flush())
    assert 48 <= count <= 50
    whole = TwilioAudio()
    whole_count = len(whole.to_twilio(second)) + len(whole.flush())
    assert abs(count - whole_count) <= 1
