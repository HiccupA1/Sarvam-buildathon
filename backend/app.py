"""Phase 5: instrumentation.

Adds per-utterance latency logging to logs/metrics.jsonl (room,
speaker/listener, languages, stt_ms/translate_ms/tts_ms/total_ms).
stt_ms is speech_start -> transcript.final, a real measured value from
the VAD events Sarvam already sends. translate_ms and tts_ms are the
two API call durations. total_ms sums the three; it does NOT include
mic-capture/network transit or client-side playback-start delay, since
that needs client-reported timestamps that aren't wired up. Running an
actual structured multi-language test set and reporting real numbers
from it (per the build plan) is a separate step requiring live mic
input -- not done here.

Phase 4 (two-pane observer UI): transcript, VAD, and
translated-caption events are now broadcast to *both* participants
(tagged with the originating/listening party_id), not just to the
party they came from. This lets a single shared screen render both
parties' panes at once, per the demo's "only one device is visible
physically" requirement. Audio playback itself still goes only to the
intended listener -- broadcasting audio too would make both devices
play sound meant for only one side.

Commit policy (from Phase 3): explicit, documented VAD tuning (see
VAD_* constants below) instead of relying on Sarvam's defaults.

Ordering (from Phase 3): each participant's committed utterances are
translated/synthesized by a single ordered worker (translate_queue),
not fire-and-forget asyncio.create_task calls. Without this, two quick
utterances could race and arrive at the peer out of order, and the
last utterance of a call could be silently dropped if the socket
closed before its background task finished.

Barge-in policy: the *sender* side is strictly ordered (above).
The *receiver* side (whether new audio interrupts still-playing audio,
or queues behind it) is a frontend decision -- see frontend/index.html,
which interrupts: newly arrived audio stops whatever is currently
playing. Rationale: queuing indefinitely would let translation lag
compound across a long conversation; interrupting keeps lag bounded at
the cost of occasionally clipping the tail of a translation.

Integration verified against docs on 2026-09-26 (see ../phase0_realtime_stt.py)
plus sarvamai 0.1.34's own signatures:
  - speech_to_text_realtime_streaming.connect(mode="transcribe", ...)
  - text.translate(source_language_code=..., target_language_code=...)
  - text_to_speech.convert(output_audio_codec="wav", ...)

No reconnect handling yet -- a dropped socket removes that participant
from the room; that's a later phase per the build plan (session
lifecycle / reconnects).
"""

import asyncio
import base64
import itertools
import json
import os
import sys
import time
from datetime import datetime, timezone

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

# Commit policy: an utterance becomes transcript.final after this much
# trailing silence. Lower = less perceived lag before translation
# starts, but risks committing mid-sentence on a natural thinking-pause,
# producing a fragmented translation that's already been spoken aloud
# to the other party before a fuller transcript could correct it.
# 500ms is Sarvam's own default; kept explicit here so it's a
# deliberate, tunable choice rather than an implicit one, per the
# structured latency/accuracy test set planned for later.
VAD_SILENCE_DURATION_MS = 500
VAD_THRESHOLD = 0.3
VAD_MIN_SPEECH_DURATION_MS = 250

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
METRICS_LOG_PATH = os.path.join(LOG_DIR, "metrics.jsonl")

app = FastAPI()
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def log_metrics(record: dict) -> None:
    """Append one utterance's timing to logs/metrics.jsonl.

    This is the raw material for the structured latency/accuracy test
    set the build plan calls for -- it's captured from day one here,
    but actually running a scripted multi-language test set and
    reporting real numbers from it is a separate, deliberately-not-yet-
    done step (it needs live mic input, not synthetic test audio).
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    with open(METRICS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


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

    async def broadcast_json_safe(self, payload: dict) -> None:
        for p in list(self.participants.values()):
            await p.send_json_safe(payload)


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
    await participant.send_json_safe({"type": "joined", "party_id": party_id, "language": language})
    peer = session.other(party_id)
    if peer is not None:
        await peer.send_json_safe({"type": "peer_joined", "party_id": party_id, "language": language})
        await participant.send_json_safe({"type": "peer_joined", "party_id": peer.party_id, "language": peer.language})

    client = AsyncSarvamAI(api_subscription_key=API_KEY)
    translate_queue: asyncio.Queue = asyncio.Queue()
    seq_counter = itertools.count(1)

    async def translate_worker():
        while True:
            item = await translate_queue.get()
            if item is None:
                translate_queue.task_done()
                break
            text, seq, stt_ms = item
            current_peer = session.other(party_id)
            if current_peer is not None:
                await translate_and_speak(
                    text, language, current_peer, session, client, seq, stt_ms, room_id, party_id
                )
            translate_queue.task_done()

    worker_task = asyncio.create_task(translate_worker())

    try:
        async with client.speech_to_text_realtime_streaming.connect(
            language_code=language,
            model="saaras:v3-realtime",
            stream_type="fast",
            mode="transcribe",
            encoding="linear16",
            sample_rate=str(SAMPLE_RATE),
            threshold=str(VAD_THRESHOLD),
            silence_duration_ms=str(VAD_SILENCE_DURATION_MS),
            min_speech_duration_ms=str(VAD_MIN_SPEECH_DURATION_MS),
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

            # Speech-start timestamp for the utterance currently in progress,
            # used to derive an honest "STT commit" latency (speech_start ->
            # transcript.final) since the non-streaming TTS call we use has
            # no first-byte signal to anchor a fuller stage breakdown on.
            utterance_state = {"start_ts": None}

            async def handle_stt_events():
                async for message in stt_ws:
                    if message.event == "transcript.partial":
                        # Broadcast (not just to self) so a single shared
                        # screen can show both parties' panes at once, per
                        # the demo's "one visible device" requirement.
                        await session.broadcast_json_safe(
                            {"type": "transcript", "party": party_id, "kind": "partial", "text": message.text}
                        )
                    elif message.event == "transcript.final":
                        if not message.text.strip():
                            continue
                        seq = next(seq_counter)
                        stt_ms = None
                        if utterance_state["start_ts"] is not None:
                            stt_ms = round((time.perf_counter() - utterance_state["start_ts"]) * 1000)
                        utterance_state["start_ts"] = None
                        await session.broadcast_json_safe(
                            {"type": "transcript", "party": party_id, "kind": "final", "text": message.text, "seq": seq}
                        )
                        await translate_queue.put((message.text, seq, stt_ms))
                    elif message.event in ("vad.speech_start", "vad.speech_end"):
                        state = "start" if message.event == "vad.speech_start" else "end"
                        if state == "start" and utterance_state["start_ts"] is None:
                            utterance_state["start_ts"] = time.perf_counter()
                        await session.broadcast_json_safe({"type": "vad", "party": party_id, "state": state})
                    elif message.event == "error":
                        await participant.send_json_safe({"type": "error", "message": message.message})
                        if message.is_fatal:
                            break

            await asyncio.gather(relay_audio(), handle_stt_events())
    except WebSocketDisconnect:
        pass
    finally:
        # Drain any in-flight utterances so the last thing said isn't
        # silently dropped just because the mic/STT side finished (or
        # errored, e.g. connect() itself failing) before translation
        # caught up. Safe to run even if the queue was never touched.
        await translate_queue.put(None)
        await worker_task

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


async def translate_and_speak(
    text, source_language, listener: "Participant", session: "Session", client, seq, stt_ms, room_id, speaker_party_id
):
    target_language = listener.language
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
    # Broadcast the caption (both panes can show it), but the audio
    # itself goes only to the listener -- the other device shouldn't
    # also play back audio meant for its peer.
    await session.broadcast_json_safe({"type": "translated", "party": listener.party_id, "text": translated, "seq": seq})

    tts = await client.text_to_speech.convert(
        text=translated,
        language_code=target_language,
        speaker=TTS_SPEAKER,
        model="bulbul:v3",
        output_audio_codec="wav",
        speech_sample_rate=SAMPLE_RATE,
    )
    t2 = time.perf_counter()
    translate_ms = round((t1 - t0) * 1000)
    tts_ms = round((t2 - t1) * 1000)
    # total_ms is speech-end-to-audio-ready when we have a real stt_ms
    # (speech_start -> transcript.final); otherwise falls back to just
    # the translate+TTS portion. Doesn't include audio capture/network
    # or playback-start delay -- those need client-reported timestamps,
    # which aren't wired up yet.
    total_ms = (stt_ms or 0) + translate_ms + tts_ms
    print(f"[latency] seq={seq} stt={stt_ms}ms translate={translate_ms}ms tts={tts_ms}ms total={total_ms}ms")
    await session.broadcast_json_safe({
        "type": "latency",
        "party": listener.party_id,
        "seq": seq,
        "stt_ms": stt_ms,
        "translate_ms": translate_ms,
        "tts_ms": tts_ms,
        "total_ms": total_ms,
    })
    log_metrics({
        "room": room_id,
        "speaker_party": speaker_party_id,
        "listener_party": listener.party_id,
        "source_language": source_language,
        "target_language": target_language,
        "seq": seq,
        "text_chars": len(text),
        "stt_ms": stt_ms,
        "translate_ms": translate_ms,
        "tts_ms": tts_ms,
        "total_ms": total_ms,
    })

    await listener.send_json_safe({"type": "audio", "party": listener.party_id, "format": "wav", "data": tts.audios[0], "seq": seq})
