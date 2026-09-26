"""Phase 0 checkpoint: stream a short audio clip through Sarvam realtime STT.

Integration verified against these docs on 2026-09-26:
  - https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming.md
  - https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/overview.md

The test clip is synthesized with TTS so the check needs no recorded audio.
"""

import asyncio
import base64
import os
import sys
import time

from dotenv import load_dotenv
from sarvamai import AsyncSarvamAI, RealtimeAudioInput, RealtimeEnd, SarvamAI

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

API_KEY = os.environ.get("SARVAM_API_KEY")
if not API_KEY:
    sys.exit("SARVAM_API_KEY is not set. Add it to .env and try again.")

SAMPLE_RATE = 16000
# 3200 bytes = ~100ms of 16kHz mono 16-bit PCM, the chunk size the docs recommend.
CHUNK_BYTES = 3200

SOURCE_LANGUAGE = "ta-IN"
SOURCE_TEXT = "வணக்கம், நான் இன்று காலை சந்தைக்கு சென்றேன்."


def synthesize_test_clip() -> bytes:
    client = SarvamAI(api_subscription_key=API_KEY)
    response = client.text_to_speech.convert(
        text=SOURCE_TEXT,
        language_code=SOURCE_LANGUAGE,
        # The SDK's speaker Literal mixes v2 and v3 names; this one is v3-compatible.
        speaker="kavitha",
        model="bulbul:v3",
        output_audio_codec="linear16",
        speech_sample_rate=SAMPLE_RATE,
    )
    return base64.b64decode(response.audios[0])


async def stream_through_stt(pcm: bytes, mode: str) -> None:
    client = AsyncSarvamAI(api_subscription_key=API_KEY)
    async with client.speech_to_text_realtime_streaming.connect(
        language_code=SOURCE_LANGUAGE,
        model="saaras:v3-realtime",
        stream_type="fast",
        mode=mode,
        encoding="linear16",
        sample_rate=str(SAMPLE_RATE),
    ) as ws:
        send_started = time.perf_counter()

        async def send_audio():
            for offset in range(0, len(pcm), CHUNK_BYTES):
                chunk = pcm[offset : offset + CHUNK_BYTES]
                await ws.send_realtime_audio_input(
                    RealtimeAudioInput(audio=base64.b64encode(chunk).decode("utf-8"))
                )
                # Pace the send to real time so latency numbers mean something.
                await asyncio.sleep(CHUNK_BYTES / (SAMPLE_RATE * 2))
            await ws.send_realtime_end(RealtimeEnd())

        async def receive_events():
            first_partial = None
            async for message in ws:
                elapsed = time.perf_counter() - send_started
                if message.event == "transcript.partial":
                    if first_partial is None:
                        first_partial = elapsed
                    print(f"  [{elapsed:6.2f}s] partial: {message.text}")
                elif message.event == "transcript.final":
                    print(f"  [{elapsed:6.2f}s] FINAL:   {message.text}")
                    if first_partial is not None:
                        print(f"  first partial at {first_partial:.2f}s")
                    return
                elif message.event == "error":
                    print(f"  error ({message.code}): {message.message}")
                    if message.is_fatal:
                        return

        await asyncio.gather(send_audio(), receive_events())


async def main() -> None:
    print(f"Synthesizing test clip ({SOURCE_LANGUAGE}): {SOURCE_TEXT}")
    pcm = synthesize_test_clip()
    duration = len(pcm) / (SAMPLE_RATE * 2)
    print(f"Clip ready: {len(pcm)} bytes, {duration:.2f}s\n")

    # Compare a plain transcript against STT's own translate mode, which returns
    # target-language text directly and skips a separate translation call.
    for mode in ("transcribe", "translate"):
        print(f"--- mode={mode} ---")
        await stream_through_stt(pcm, mode)
        print()


if __name__ == "__main__":
    asyncio.run(main())
