"""Converts between Twilio phone audio and the Gemini Live audio formats.

Twilio:  G.711 mu-law, 8 kHz, mono, 160-byte frames (20 ms), base64 in JSON.
Gemini:  16-bit little-endian PCM, mono; 16 kHz in, 24 kHz out.
"""
import base64
import binascii
import warnings

with warnings.catch_warnings():
    # audioop is deprecated in 3.11 and 3.12 and removed in 3.13; this project pins below 3.13.
    warnings.simplefilter("ignore", DeprecationWarning)
    import audioop

TWILIO_FRAME_BYTES = 160
GEMINI_INPUT_MIME = "audio/pcm;rate=16000"
_WIDTH = 2  # bytes per PCM sample
_ULAW_SILENCE = b"\xff"


class TwilioAudio:
    """Holds resampler state for one call, so audio stays continuous across frames."""

    def __init__(self) -> None:
        self._in_state = None
        self._out_state_24_to_16 = None
        self._out_state_16_to_8 = None
        self._odd_byte = b""
        self._held = b""

    def to_gemini(self, payload_b64: str) -> bytes:
        try:
            ulaw = base64.b64decode(payload_b64)
        except (binascii.Error, ValueError, TypeError):
            return b""  # the payload arrives over a network; never let a bad one raise
        if not ulaw:
            return b""
        pcm_8k = audioop.ulaw2lin(ulaw, _WIDTH)
        pcm_16k, self._in_state = audioop.ratecv(pcm_8k, _WIDTH, 1, 8000, 16000, self._in_state)
        return pcm_16k

    def to_twilio(self, pcm_24k: bytes) -> list[str]:
        data = self._odd_byte + pcm_24k
        if len(data) % _WIDTH:
            data, self._odd_byte = data[:-1], data[-1:]
        else:
            self._odd_byte = b""
        if data:
            # two steps, as in Google's telephony sample, to reduce aliasing
            pcm_16k, self._out_state_24_to_16 = audioop.ratecv(
                data, _WIDTH, 1, 24000, 16000, self._out_state_24_to_16
            )
            pcm_8k, self._out_state_16_to_8 = audioop.ratecv(
                pcm_16k, _WIDTH, 1, 16000, 8000, self._out_state_16_to_8
            )
            self._held += audioop.lin2ulaw(pcm_8k, _WIDTH)
        frames = []
        while len(self._held) >= TWILIO_FRAME_BYTES:
            frame, self._held = self._held[:TWILIO_FRAME_BYTES], self._held[TWILIO_FRAME_BYTES:]
            frames.append(base64.b64encode(frame).decode())
        return frames

    def flush(self) -> list[str]:
        if not self._held:
            return []
        frame = self._held.ljust(TWILIO_FRAME_BYTES, _ULAW_SILENCE)
        self._held = b""
        return [base64.b64encode(frame).decode()]

    def reset_output(self) -> None:
        self._held = b""
        self._odd_byte = b""
