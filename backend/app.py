"""Phase 7: decoupled speak/hear languages.

A participant's selected `language` is now *only* the language they want
to hear in, not the language they're speaking. Each participant can speak
any supported language, switch mid-conversation, or code-mix (e.g.
Tamil-English) freely -- the model figures out what was said and each
listener gets it in whatever they personally selected.

This means:
  - STT connects with language_code="auto" (not the participant's
    listening preference) and mode="codemix", so it adapts to whatever
    the speaker actually says instead of being pinned to one language.
    Sarvam's own docs don't fully specify how "auto" behaves with
    intra-utterance code-switching (checked live on 2026-09-27, not
    documented in detail) -- codemix mode is the closest documented fit
    for a speaker who mixes languages within their speech.
  - Translation always passes source_language_code="auto" too, since
    the speaker's actual language is no longer known ahead of time (it
    can vary utterance to utterance, or within one). This removes the
    same-language shortcut _speak_one_language used to have -- every
    utterance now gets a real translate() call per target-language
    group, even on the (now unknown until Mayura detects it) chance
    that speaker and listener happen to be using the same language.

Phase 6 (conference mode): up to MAX_PARTICIPANTS participants per room.
Each participant still runs their own one-directional STT pipeline, but
a committed utterance fans out to every *other* participant, translated
into each listener's own selected (hearing) language.

Key design point: with up to 5 people but only 4 supported languages,
at least two participants must share a language once the room has 5
people (pigeonhole). So utterances are translated/synthesized once per
*distinct target language* among the other participants, not once per
listener -- e.g. if 3 of 4 listeners picked Hindi, that's one translate
call + one TTS call serving all 3, not three. The distinct-language
groups are then processed concurrently (asyncio.gather), so a listener's
latency doesn't scale with how many *other* languages happen to be in
the room.

Broadcast scope, generalized from the two-party version: transcript and
VAD events (in the speaker's own language) still go to everyone, so any
device can show a full participant grid. Translated captions, latency,
and audio are now sent only to the specific listeners in that language
group -- not broadcast room-wide -- since they're meaningless (or in
audio's case, actively wrong) for participants in a different language
group.

Commit policy (from Phase 3): explicit, documented VAD tuning (see
VAD_* constants below) instead of relying on Sarvam's defaults.

Ordering (from Phase 3): each participant's committed utterances are
translated/synthesized by a single ordered worker (translate_queue),
not fire-and-forget asyncio.create_task calls. Without this, two quick
utterances could race and arrive at listeners out of order, and the
last utterance of a call could be silently dropped if the socket
closed before its background task finished.

Each speaker has an independent STT stream and ordered translation worker.
At most two speaker microphones can be unmuted per room. Explicit server
mute enforcement replaces PCM with silence; listening remains available.
The frontend uses a bounded FIFO for translated audio rather than cutting
off another speaker's translation. Queue delay is included in playback timing.

Integration verified against docs on 2026-09-26 (see ../phase0_realtime_stt.py)
plus sarvamai 0.1.34's own signatures:
  - speech_to_text_realtime_streaming.connect(mode="transcribe", ...)
  - text.translate(source_language_code=..., target_language_code=...)
  - text_to_speech.convert(output_audio_codec="wav", ...)

Short reconnects retain the participant and upstream STT session within
a bounded grace period. Server restarts still require a fresh session.
"""

import asyncio
import base64
import itertools
import json
import os
import re
import secrets
import struct
import time
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sarvamai import AsyncSarvamAI, RealtimeAudioInput, RealtimeEnd

from backend.budget import configured_budget, BudgetExceeded

load_dotenv()
BUDGET = configured_budget()

API_KEY = os.environ.get("SARVAM_API_KEY")
LIVE_ENABLED = os.getenv("LIVE_ENABLED", "false").lower() == "true"
ACCESS_TOKEN = os.getenv("DEMO_ACCESS_TOKEN", "")
ALLOWED_ORIGINS = set(filter(None, os.getenv("ALLOWED_ORIGINS", "").split(",")))
MAX_ROOMS = int(os.getenv("MAX_ROOMS", "2"))
SESSION_SECONDS = int(os.getenv("SESSION_SECONDS", "120"))
RECONNECT_SECONDS = float(os.getenv("RECONNECT_SECONDS", "10"))
LANGUAGES = {"hi-IN", "en-IN", "ta-IN", "te-IN"}
if LIVE_ENABLED and (not API_KEY or len(ACCESS_TOKEN) < 16):
    raise RuntimeError("Live mode requires SARVAM_API_KEY and a DEMO_ACCESS_TOKEN of at least 16 characters")
if int(os.getenv("WEB_CONCURRENCY", "1")) != 1:
    raise RuntimeError("Room routing requires exactly one worker")

SAMPLE_RATE = 16000
# bulbul:v3 speaker; verified compatible in phase0_realtime_stt.py after
# the SDK's speaker Literal turned out to mix v2/v3 names.
TTS_SPEAKER = "kavitha"

MAX_PARTICIPANTS = 5
PARTY_LABELS = "ABCDE"

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

app = FastAPI(docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def log_metrics(record: dict) -> None:
    """Append one utterance's timing to logs/metrics.jsonl.

    Runtime metrics complement the checkpointed synthetic benchmark.
    Human conversation quality still requires native-speaker review.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    with open(METRICS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


@app.get("/")
async def index():
    # Deliver capture-critical assets atomically with the page, even on a tunnel.
    with open(os.path.join(FRONTEND_DIR, "index.html"), encoding="utf-8") as source:
        html = source.read()
    with open(os.path.join(FRONTEND_DIR, "audio-processor.js"), encoding="utf-8") as source:
        worklet = json.dumps(source.read()).replace("<", "\\u003c")
    html = html.replace('"__PCM_WORKLET_SOURCE__"', worklet)
    for icon in ("mic", "mic-off"):
        with open(os.path.join(FRONTEND_DIR, "icons", f"{icon}.svg"), encoding="utf-8") as source:
            html = html.replace(f"<!-- INLINE_{icon.upper()} -->", source.read())
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health():
    return {"status": "ok", "active_rooms": len(SESSIONS)}


@app.get("/config")
async def config():
    return {"live_enabled": LIVE_ENABLED, "session_seconds": SESSION_SECONDS}


class Participant:
    def __init__(self, party_id: str, websocket: WebSocket, language: str):
        self.party_id = party_id
        self.websocket = websocket
        self.language = language
        self._send_lock = asyncio.Lock()
        self.resume_token = secrets.token_urlsafe(32)
        self.connected = asyncio.Event()
        self.connected.set()
        self.closed = asyncio.Event()
        self.detached = False
        self.capture_start_ms = None
        self.audio_protocol = "pcm16"
        self.playback_records = {}
        self.role = "speaker"
        self.muted = False

    async def receive(self):
        while True:
            socket = self.websocket
            try:
                message = await socket.receive()
            except (WebSocketDisconnect, RuntimeError):
                message = {"type": "websocket.disconnect"}
            if message["type"] != "websocket.disconnect":
                return message
            self.detached = True
            self.connected.clear()
            # The replacement may have arrived while the old receive was pending.
            if self.websocket is not socket:
                self.detached = False
                continue
            await asyncio.wait_for(self.connected.wait(), RECONNECT_SECONDS)
            self.detached = False

    async def send_json_safe(self, payload: dict) -> None:
        async with self._send_lock:
            try:
                await self.websocket.send_json(payload)
            except (RuntimeError, WebSocketDisconnect, OSError):
                pass  # socket already closed


class Session:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.participants: dict[str, Participant] = {}

    def other_participants(self, party_id: str) -> list[Participant]:
        return [p for pid, p in self.participants.items() if pid != party_id]

    def set_microphone(self, participant: Participant, muted: bool) -> bool:
        if participant.role != "speaker":
            return False
        if not muted and sum(p.role == "speaker" and not p.muted
                             for p in self.other_participants(participant.party_id)) >= 2:
            return False
        participant.muted = muted
        if muted:
            participant.capture_start_ms = None
        return True

    async def broadcast_json_safe(self, payload: dict) -> None:
        for p in list(self.participants.values()):
            await p.send_json_safe(payload)

    @staticmethod
    async def send_to_json_safe(participants: list[Participant], payload: dict) -> None:
        for p in participants:
            await p.send_json_safe(payload)


SESSIONS: dict[str, Session] = {}
SESSIONS_LOCK = asyncio.Lock()


@app.websocket("/ws/{room_id}")
async def ws_endpoint(websocket: WebSocket, room_id: str):
    await websocket.accept()

    try:
        if not LIVE_ENABLED or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", room_id):
            raise ValueError("Live demo unavailable or invalid room code")
        origin = websocket.headers.get("origin")
        if origin and origin not in (ALLOWED_ORIGINS or {f"{websocket.url.scheme.replace('ws', 'http')}://{websocket.headers.get('host')}"}):
            raise ValueError("Origin not allowed")
        join = json.loads(await asyncio.wait_for(websocket.receive_text(), 5))
        if not isinstance(join, dict):
            raise ValueError("Invalid join")
        if join.get("type") != "join" or not secrets.compare_digest(str(join.get("access_token", "")), ACCESS_TOKEN):
            raise ValueError("Invalid demo invite")
        hearing_language = join["language"]  # what THIS participant wants to hear, not what they'll speak
        if hearing_language not in LANGUAGES or join.get("role", "speaker") not in {"speaker", "listener"}:
            raise ValueError("Invalid language or role")
    except (ValueError, KeyError, TypeError, asyncio.TimeoutError):
        await websocket.send_json({"type": "error", "message": "Join rejected: check demo availability, invite, room and language"})
        await websocket.close(code=1008)
        return
    except WebSocketDisconnect:
        return

    resumed = False
    async with SESSIONS_LOCK:
        room = SESSIONS.get(room_id)
        resume_token = join.get("resume_token")
        participant = next((p for p in room.participants.values() if resume_token and secrets.compare_digest(p.resume_token, str(resume_token))), None) if room else None
        if participant:
            if not participant.detached or participant.closed.is_set():
                await websocket.close(code=1008)
                return
            participant.websocket = websocket
            participant.connected.set()
            participant.detached = False
            session = room
            party_id = participant.party_id
            hearing_language = participant.language
            resumed = True
        elif len(SESSIONS) >= MAX_ROOMS and room is None:
            await websocket.close(code=1008)
            return
        else:
            session = SESSIONS.setdefault(room_id, Session(room_id))
            if len(session.participants) >= MAX_PARTICIPANTS:
                await websocket.send_json({"type": "error", "message": f"room is full (max {MAX_PARTICIPANTS} participants)"})
                await websocket.close(code=1008)
                return
            party_id = next(label for label in PARTY_LABELS if label not in session.participants)
            participant = Participant(party_id, websocket, hearing_language)
            participant.role = join.get("role", "speaker")
            participant.muted = True
            if participant.role == "speaker" and not join.get("muted", False):
                session.set_microphone(participant, False)
            participant.audio_protocol = join.get("audio_protocol", "pcm16")
            session.participants[party_id] = participant

    print(f"[room {room_id}] {party_id} joined (hears {hearing_language}) -- {len(session.participants)}/{MAX_PARTICIPANTS}")
    await participant.send_json_safe({"type": "joined", "party_id": party_id, "language": hearing_language,
                                     "resume_token": participant.resume_token, "resumed": resumed,
                                     "muted": participant.muted, "role": participant.role})

    # Tell the newcomer about everyone already in the room, and tell
    # everyone already in the room about the newcomer.
    existing = session.other_participants(party_id)
    for other in existing:
        await participant.send_json_safe({"type": "peer_joined", "party_id": other.party_id, "language": other.language,
                                          "muted": other.muted, "role": other.role})
    if resumed:
        await participant.closed.wait()
        return
    for other in existing:
        await other.send_json_safe({"type": "peer_joined", "party_id": party_id, "language": hearing_language,
                                    "muted": participant.muted, "role": participant.role})

    client = None
    http_client = None
    translate_queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    seq_counter = itertools.count(1)

    async def translate_worker():
        while True:
            item = await translate_queue.get()
            if item is None:
                translate_queue.task_done()
                break
            text, seq, stt_ms, capture_start_ms, committed_at = item
            queue_ms = round((time.perf_counter() - committed_at) * 1000)
            listeners = session.other_participants(party_id)
            if listeners:
                await translate_and_speak_to_listeners(
                    text, listeners, client, seq, stt_ms, room_id, party_id, capture_start_ms, queue_ms
                )
            translate_queue.task_done()

    worker_task = asyncio.create_task(translate_worker())
    relay_task = stt_task = None

    async def handle_control(control):
        if control.get("type") == "microphone" and isinstance(control.get("muted"), bool):
            async with SESSIONS_LOCK:
                accepted = session.set_microphone(participant, control["muted"])
            payload = {"type": "microphone", "party_id": party_id, "muted": participant.muted,
                       "role": participant.role, "accepted": accepted}
            if accepted:
                await session.broadcast_json_safe(payload)
            else:
                payload["message"] = "Two microphones are already unmuted" if participant.role == "speaker" else "Listen-only session"
                await participant.send_json_safe(payload)
        elif control.get("type") == "ping":
            await participant.send_json_safe({"type": "pong", "client_ms": control.get("client_ms"), "server_ms": time.time() * 1000})
        elif control.get("type") == "playback":
            key = (control.get("speaker"), control.get("seq"))
            expected = participant.playback_records.pop(key, None)
            delay = control.get("receive_to_playback_ms")
            if expected and isinstance(delay, (int, float)) and 0 <= delay <= 60000:
                log_metrics({"kind": "playback", "room": room_id, "listener": party_id,
                             "speaker": key[0], "seq": key[1], "receive_to_playback_ms": round(delay),
                             "capture_to_playback_ms": control.get("capture_to_playback_ms"),
                             "clock_rtt_ms": control.get("clock_rtt_ms")})

    async def listener_loop():
        while True:
            message = await participant.receive()
            if message.get("text"):
                control = json.loads(message["text"])
                if control.get("type") == "end":
                    return
                await handle_control(control)

    try:
        if join.get("role", "speaker") == "listener":
            await participant.send_json_safe({"type": "ready"})
            await asyncio.wait_for(listener_loop(), SESSION_SECONDS)
            return
        # Reserve the entire bounded upstream session before opening it.
        # Counting only PCM misses potentially billable idle connection time.
        BUDGET.reserve("stt_seconds", SESSION_SECONDS)
        http_client = httpx.AsyncClient(timeout=15, follow_redirects=True)
        client = AsyncSarvamAI(api_subscription_key=API_KEY, httpx_client=http_client)
        # language_code="auto" + mode="codemix": this participant can speak
        # any supported language, switch mid-conversation, or mix languages
        # within an utterance -- their own `hearing_language` above governs
        # only what THEY hear, never what they're allowed to speak.
        connect_start = time.perf_counter()
        stt_connect_cm = client.speech_to_text_realtime_streaming.connect(
            language_code="auto",
            model="saaras:v3-realtime",
            stream_type="fast",
            mode="codemix",
            encoding="linear16",
            sample_rate=str(SAMPLE_RATE),
            threshold=str(VAD_THRESHOLD),
            silence_duration_ms=str(VAD_SILENCE_DURATION_MS),
            min_speech_duration_ms=str(VAD_MIN_SPEECH_DURATION_MS),
        )
        async with asyncio.timeout(SESSION_SECONDS), stt_connect_cm as stt_ws:
            await participant.send_json_safe({"type": "ready"})
            connect_ms = round((time.perf_counter() - connect_start) * 1000)
            print(f"[connect] room={room_id} party={party_id} stt_connect_ms={connect_ms}")
            log_metrics({"kind": "connect", "room": room_id, "party": party_id, "connect_ms": connect_ms})

            async def relay_audio():
                try:
                    while True:
                        message = await participant.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        audio_bytes = message.get("bytes")
                        if audio_bytes is not None:
                            if len(audio_bytes) > 64008:
                                raise ValueError("Audio frame too large")
                            if participant.audio_protocol == "timestamped_pcm16":
                                if len(audio_bytes) < 10:
                                    raise ValueError("Invalid timestamped audio")
                                captured_ms = struct.unpack("<d", audio_bytes[:8])[0]
                                audio_bytes = audio_bytes[8:]
                                if len(audio_bytes) % 2:
                                    raise ValueError("Invalid PCM")
                                samples = struct.unpack(f"<{len(audio_bytes) // 2}h", audio_bytes)
                                if not participant.muted and participant.capture_start_ms is None and any(abs(s) > 400 for s in samples):
                                    if abs(time.time() * 1000 - captured_ms) < 60000:
                                        participant.capture_start_ms = captured_ms
                            if participant.muted:
                                audio_bytes = bytes(len(audio_bytes))
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
                            await handle_control(control)
                except WebSocketDisconnect:
                    pass
                finally:
                    try:
                        await stt_ws.send_realtime_end(RealtimeEnd())
                    except Exception:
                        pass  # STT connection may already be closing/closed

            # Speech-start timestamp for the utterance currently in progress,
            # used to derive an honest "STT commit" latency (speech_start ->
            # transcript.final) since the non-streaming TTS call we use has
            # no first-byte signal to anchor a fuller stage breakdown on.
            utterance_state = {"start_ts": None}

            async def handle_stt_events():
                async for message in stt_ws:
                    if message.event == "transcript.partial":
                        # Broadcast (not just to self) so any device can
                        # show a full participant grid, per the demo's
                        # "one visible device" requirement.
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
                        capture_start_ms = participant.capture_start_ms
                        participant.capture_start_ms = None
                        await translate_queue.put((message.text, seq, stt_ms, capture_start_ms, time.perf_counter()))
                    elif message.event in ("vad.speech_start", "vad.speech_end"):
                        state = "start" if message.event == "vad.speech_start" else "end"
                        if state == "start" and utterance_state["start_ts"] is None:
                            utterance_state["start_ts"] = time.perf_counter()
                        await session.broadcast_json_safe({"type": "vad", "party": party_id, "state": state})
                    elif message.event == "error":
                        await participant.send_json_safe({"type": "error", "message": "Speech provider unavailable; session stopped"})
                        if message.is_fatal:
                            break

            # Don't use gather() here: it waits for BOTH to finish, but
            # handle_stt_events only returns once Sarvam's server closes
            # its side of the STT connection, which isn't guaranteed to
            # happen promptly (or at all) just because we sent it an end
            # signal. If it hangs, this handler never reaches the cleanup
            # below, and the room slot stays occupied forever -- the
            # "can't rejoin the same room" bug this guards against.
            relay_task = asyncio.create_task(relay_audio())
            stt_task = asyncio.create_task(handle_stt_events())
            done, pending = await asyncio.wait({relay_task, stt_task}, timeout=SESSION_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await participant.send_json_safe({"type": "error", "message": "Demo session time limit reached"})

            if relay_task in done and stt_task in pending:
                # Client stopped sending audio (explicit "end" or a
                # disconnect) -- this is the NORMAL end of an utterance,
                # not just a leave. Sarvam needs a moment after the last
                # audio chunk to actually deliver transcript.final; an
                # earlier version of this cancelled stt_task instantly
                # here, which cut off that final transcript every time
                # (nothing ever got translated/spoken). Give it a bounded
                # grace period before giving up on it.
                try:
                    await asyncio.wait_for(stt_task, timeout=5.0)
                except asyncio.TimeoutError:
                    stt_task.cancel()
                    try:
                        await stt_task
                    except asyncio.CancelledError:
                        pass
            elif stt_task in done and relay_task in pending:
                # STT session ended or errored on its own -- no point
                # relaying more audio to a dead connection.
                relay_task.cancel()
                try:
                    await relay_task
                except asyncio.CancelledError:
                    pass

            for task in (relay_task, stt_task):
                if task.done() and not task.cancelled():
                    exc = task.exception()
                    if exc is not None:
                        raise exc
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log_metrics({"kind": "session_error", "error_type": type(exc).__name__})
        await participant.send_json_safe({"type": "error", "message": str(exc) if isinstance(exc, BudgetExceeded) else "Session stopped; please try again later"})
    finally:
        for task in (relay_task, stt_task):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (relay_task, stt_task) if t), return_exceptions=True)
        # Drain any in-flight utterances so the last thing said isn't
        # silently dropped just because the mic/STT side finished (or
        # errored, e.g. connect() itself failing) before translation
        # caught up. Safe to run even if the queue was never touched.
        try:
            await asyncio.wait_for(translate_queue.put(None), 1)
            await asyncio.wait_for(worker_task, 35)
        except (Exception, asyncio.CancelledError):
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)

        remaining_participants: list[Participant] = []
        async with SESSIONS_LOCK:
            room = SESSIONS.get(room_id)
            if room and party_id in room.participants:
                del room.participants[party_id]
                remaining_participants = list(room.participants.values())
                if not room.participants:
                    del SESSIONS[room_id]
        await Session.send_to_json_safe(remaining_participants, {"type": "peer_left", "party_id": party_id})
        print(f"[room {room_id}] {party_id} left -- {len(remaining_participants)}/{MAX_PARTICIPANTS} remain")
        try:
            await participant.websocket.close(code=1000)
        except (RuntimeError, WebSocketDisconnect):
            pass  # client already closed its side
        if http_client is not None:
            await http_client.aclose()
        participant.closed.set()


async def translate_and_speak_to_listeners(
    text: str, listeners: list[Participant], client, seq: int, stt_ms, room_id: str, speaker_party_id: str, capture_start_ms=None, queue_ms=0
) -> None:
    """Fan out one committed utterance to every listener, once per distinct
    target language among them (not once per listener -- see module
    docstring). The per-language branches run concurrently so a listener's
    latency doesn't scale with how many other languages are in the room.
    """
    by_language: dict[str, list[Participant]] = {}
    for listener in listeners:
        by_language.setdefault(listener.language, []).append(listener)

    outcomes = await asyncio.gather(*(
        _speak_one_language(text, target_language, group, client, seq, stt_ms, room_id, speaker_party_id, capture_start_ms, queue_ms)
        for target_language, group in by_language.items()
    ), return_exceptions=True)
    for (language, group), outcome in zip(by_language.items(), outcomes):
        if isinstance(outcome, Exception):
            await Session.send_to_json_safe(group, {"type": "error", "message":
                str(outcome) if isinstance(outcome, BudgetExceeded) else "Translation unavailable for this utterance"})
            log_metrics({"kind": "pipeline_error", "room": room_id, "seq": seq,
                         "target_language": language, "error_type": type(outcome).__name__})


async def _speak_one_language(
    text: str, target_language: str, group: list[Participant],
    client, seq: int, stt_ms, room_id: str, speaker_party_id: str, capture_start_ms=None, queue_ms=0
) -> None:
    t0 = time.perf_counter()
    # source_language_code="auto": the speaker's language is no longer
    # pinned to anything (see module docstring) -- it can vary utterance
    # to utterance, or mix within one, so there's no known-in-advance
    # language to compare against target_language for a same-language
    # shortcut. Mayura detects the source itself from the text.
    if len(text) > 2000:
        raise ValueError("Transcript exceeds demo limit")
    BUDGET.reserve("translate_chars", len(text))
    translation = await asyncio.wait_for(client.text.translate(
        input=text,
        source_language_code="auto",
        target_language_code=target_language,
        model="mayura:v1",
    ), 15)
    translated = translation.translated_text
    t1 = time.perf_counter()

    await Session.send_to_json_safe(group, {
        "type": "translated", "speaker": speaker_party_id, "language": target_language, "text": translated, "seq": seq,
    })

    BUDGET.reserve("tts_chars", len(translated))
    tts = await asyncio.wait_for(client.text_to_speech.convert(
        text=translated,
        language_code=target_language,
        speaker=TTS_SPEAKER,
        model="bulbul:v3",
        output_audio_codec="wav",
        speech_sample_rate=SAMPLE_RATE,
    ), 15)
    t2 = time.perf_counter()
    translate_ms = round((t1 - t0) * 1000)
    tts_ms = round((t2 - t1) * 1000)
    # total_ms is speech-start-to-audio-ready when we have a real stt_ms
    # (speech_start -> transcript.final); otherwise falls back to just
    # the translate+TTS portion. Doesn't include audio capture/network
    # or playback-start delay -- those need client-reported timestamps,
    # which aren't wired up yet.
    total_ms = (stt_ms or 0) + queue_ms + translate_ms + tts_ms
    listener_ids = [p.party_id for p in group]
    print(
        f"[latency] seq={seq} speaker={speaker_party_id} -> {listener_ids} ({target_language}) "
        f"stt={stt_ms}ms translate={translate_ms}ms tts={tts_ms}ms total={total_ms}ms"
    )

    await Session.send_to_json_safe(group, {
        "type": "latency",
        "speaker": speaker_party_id,
        "language": target_language,
        "seq": seq,
        "stt_ms": stt_ms,
        "queue_ms": queue_ms,
        "translate_ms": translate_ms,
        "tts_ms": tts_ms,
        "total_ms": total_ms,
    })
    log_metrics({
        "kind": "utterance",
        "room": room_id,
        "speaker_party": speaker_party_id,
        "listener_parties": listener_ids,
        "source_language": "auto",
        "target_language": target_language,
        "seq": seq,
        "text_chars": len(text),
        "stt_ms": stt_ms,
        "queue_ms": queue_ms,
        "translate_ms": translate_ms,
        "tts_ms": tts_ms,
        "total_ms": total_ms,
    })

    for listener in group:
        if len(listener.playback_records) >= 32:
            listener.playback_records.pop(next(iter(listener.playback_records)))
        listener.playback_records[(speaker_party_id, seq)] = True
    await Session.send_to_json_safe(group, {
        "type": "audio", "speaker": speaker_party_id, "language": target_language, "format": "wav", "data": tts.audios[0], "seq": seq,
        "capture_start_ms": capture_start_ms,
    })
