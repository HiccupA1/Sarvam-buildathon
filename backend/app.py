"""Phase 2: two-party session model.

Each participant runs the same one-directional pipeline from Phase 1
(mic -> STT -> translate -> TTS), but the translated text/audio is now
routed to the *other* participant in the room instead of back to the
same socket. A room holds exactly two participants; audio flows in one
direction per speaker, through the pipeline, twice.

Integration verified against docs on 2026-09-26 (see ../phase0_realtime_stt.py)
plus sarvamai 0.1.34's own signatures:
  - speech_to_text_realtime_streaming.connect(mode="transcribe", ...)
  - text.translate(source_language_code=..., target_language_code=...)
  - text_to_speech.convert(output_audio_codec="wav", ...)

No custom commit policy yet -- this relies on STT's own VAD-based
transcript.final events. No reconnect handling yet -- a dropped socket
removes that participant from the room; that's a later phase per the
build plan (session lifecycle / reconnects).
"""

import asyncio
import base64
import json
import os
import sys
import time

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sarvamai import AsyncSarvamAI, RealtimeAudioInput, RealtimeEnd

load_dotenv()

API_KEY = os.environ.get("SARVAM_API_KEY")
if not API_KEY:
    sys.exit("SARVAM_API_KEY is not set. Add it to .env and try again.")

SAMPLE_RATE = 16000
# bulbul:v3 speaker; verified compatible in phase0_realtime_stt.py after
# the SDK's speaker Literal turned out to mix v2/v3 names.
TTS_SPEAKER = "kavitha"

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = FastAPI()
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


class Participant:
    def __init__(self, party_id: str, websocket: WebSocket, language: str):
        self.party_id = party_id
        self.websocket = websocket
        self.language = language
        self._send_lock = asyncio.Lock()

    async def send_json_safe(self, payload: dict) -> None:
        async with self._send_lock:
            try:
                await self.websocket.send_json(payload)
            except (RuntimeError, WebSocketDisconnect):
                pass  # socket already closed


class Session:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.participants: dict[str, Participant] = {}

    def other(self, party_id: str) -> Participant | None:
        for pid, p in self.participants.items():
            if pid != party_id:
                return p
        return None


SESSIONS: dict[str, Session] = {}
SESSIONS_LOCK = asyncio.Lock()


@app.websocket("/ws/{room_id}")
async def ws_endpoint(websocket: WebSocket, room_id: str):
    await websocket.accept()

    try:
        join = json.loads(await websocket.receive_text())
        language = join["language"]
    except Exception:
        await websocket.close(code=1003)
        return

    async with SESSIONS_LOCK:
        session = SESSIONS.setdefault(room_id, Session(room_id))
        if len(session.participants) >= 2:
            await websocket.send_json({"type": "error", "message": "room is full"})
            await websocket.close(code=1008)
            return
        party_id = "A" if "A" not in session.participants else "B"
        participant = Participant(party_id, websocket, language)
        session.participants[party_id] = participant

    print(f"[room {room_id}] {party_id} joined ({language})")
    await participant.send_json_safe({"type": "joined", "party_id": party_id})
    peer = session.other(party_id)
    if peer is not None:
        await peer.send_json_safe({"type": "peer_joined"})
        await participant.send_json_safe({"type": "peer_joined"})

    client = AsyncSarvamAI(api_subscription_key=API_KEY)

    try:
        async with client.speech_to_text_realtime_streaming.connect(
            language_code=language,
            model="saaras:v3-realtime",
            stream_type="fast",
            mode="transcribe",
            encoding="linear16",
            sample_rate=str(SAMPLE_RATE),
        ) as stt_ws:

            async def relay_audio():
                try:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        audio_bytes = message.get("bytes")
                        if audio_bytes is not None:
                            await stt_ws.send_realtime_audio_input(
                                RealtimeAudioInput(audio=base64.b64encode(audio_bytes).decode("utf-8"))
                            )
                            continue
                        text = message.get("text")
                        if text is not None:
                            try:
                                control = json.loads(text)
                            except ValueError:
                                continue
                            if control.get("type") == "end":
                                break
                except WebSocketDisconnect:
                    pass
                finally:
                    await stt_ws.send_realtime_end(RealtimeEnd())

            async def handle_stt_events():
                async for message in stt_ws:
                    if message.event == "transcript.partial":
                        await participant.send_json_safe({"type": "partial", "text": message.text})
                    elif message.event == "transcript.final":
                        if not message.text.strip():
                            continue
                        await participant.send_json_safe({"type": "final", "text": message.text})
                        current_peer = session.other(party_id)
                        if current_peer is not None:
                            asyncio.create_task(
                                translate_and_speak(
                                    message.text, language, current_peer.language, client, current_peer.send_json_safe
                                )
                            )
                    elif message.event == "error":
                        await participant.send_json_safe({"type": "error", "message": message.message})
                        if message.is_fatal:
                            break

            await asyncio.gather(relay_audio(), handle_stt_events())
    except WebSocketDisconnect:
        pass
    finally:
        remaining = None
        async with SESSIONS_LOCK:
            room = SESSIONS.get(room_id)
            if room and party_id in room.participants:
                del room.participants[party_id]
                remaining = next(iter(room.participants.values()), None)
                if not room.participants:
                    del SESSIONS[room_id]
        if remaining:
            await remaining.send_json_safe({"type": "peer_left"})
        print(f"[room {room_id}] {party_id} left")
        try:
            await websocket.close()
        except RuntimeError:
            pass


async def translate_and_speak(text, source_language, target_language, client, send_json_safe):
    t0 = time.perf_counter()
    if target_language == source_language:
        translated = text
    else:
        translation = await client.text.translate(
            input=text,
            source_language_code=source_language,
            target_language_code=target_language,
            model="mayura:v1",
        )
        translated = translation.translated_text
    t1 = time.perf_counter()
    await send_json_safe({"type": "translated", "text": translated})

    tts = await client.text_to_speech.convert(
        text=translated,
        language_code=target_language,
        speaker=TTS_SPEAKER,
        model="bulbul:v3",
        output_audio_codec="wav",
        speech_sample_rate=SAMPLE_RATE,
    )
    t2 = time.perf_counter()
    print(f"[latency] translate={t1 - t0:.2f}s tts={t2 - t1:.2f}s total={t2 - t0:.2f}s")

    await send_json_safe({"type": "audio", "format": "wav", "data": tts.audios[0]})
