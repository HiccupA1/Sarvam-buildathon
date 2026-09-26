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

Barge-in policy: the *sender* side is strictly ordered (above). The
*receiver* side (whether new audio interrupts still-playing audio, or
queues behind it) is a frontend decision -- see frontend/index.html,
which interrupts: newly arrived audio stops whatever is currently
playing. This now also applies across different speakers, not just
repeated utterances from one -- if two people talk over each other,
whichever translation reaches you last is what plays, matching how a
live conversation actually works.

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


@app.get("/health")
async def health():
    return {"status": "ok", "active_rooms": len(SESSIONS)}


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

    def other_participants(self, party_id: str) -> list[Participant]:
        return [p for pid, p in self.participants.items() if pid != party_id]

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
        join = json.loads(await websocket.receive_text())
        hearing_language = join["language"]  # what THIS participant wants to hear, not what they'll speak
    except Exception:
        await websocket.close(code=1003)
        return

    async with SESSIONS_LOCK:
        session = SESSIONS.setdefault(room_id, Session(room_id))
        if len(session.participants) >= MAX_PARTICIPANTS:
            await websocket.send_json({"type": "error", "message": f"room is full (max {MAX_PARTICIPANTS} participants)"})
            await websocket.close(code=1008)
            return
        party_id = next(label for label in PARTY_LABELS if label not in session.participants)
        participant = Participant(party_id, websocket, hearing_language)
        session.participants[party_id] = participant

    print(f"[room {room_id}] {party_id} joined (hears {hearing_language}) -- {len(session.participants)}/{MAX_PARTICIPANTS}")
    await participant.send_json_safe({"type": "joined", "party_id": party_id, "language": hearing_language})

    # Tell the newcomer about everyone already in the room, and tell
    # everyone already in the room about the newcomer.
    existing = session.other_participants(party_id)
    for other in existing:
        await participant.send_json_safe({"type": "peer_joined", "party_id": other.party_id, "language": other.language})
    for other in existing:
        await other.send_json_safe({"type": "peer_joined", "party_id": party_id, "language": hearing_language})

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
            listeners = session.other_participants(party_id)
            if listeners:
                await translate_and_speak_to_listeners(
                    text, listeners, client, seq, stt_ms, room_id, party_id
                )
            translate_queue.task_done()

    worker_task = asyncio.create_task(translate_worker())

    try:
        # language_code="auto" + mode="codemix": this participant can speak
        # any supported language, switch mid-conversation, or mix languages
        # within an utterance -- their own `hearing_language` above governs
        # only what THEY hear, never what they're allowed to speak.
        async with client.speech_to_text_realtime_streaming.connect(
            language_code="auto",
            model="saaras:v3-realtime",
            stream_type="fast",
            mode="codemix",
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

            # Don't use gather() here: it waits for BOTH to finish, but
            # handle_stt_events only returns once Sarvam's server closes
            # its side of the STT connection, which isn't guaranteed to
            # happen promptly (or at all) just because we sent it an end
            # signal. If it hangs, this handler never reaches the cleanup
            # below, and the room slot stays occupied forever -- the
            # "can't rejoin the same room" bug this guards against.
            relay_task = asyncio.create_task(relay_audio())
            stt_task = asyncio.create_task(handle_stt_events())
            done, pending = await asyncio.wait({relay_task, stt_task}, return_when=asyncio.FIRST_COMPLETED)

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
    finally:
        # Drain any in-flight utterances so the last thing said isn't
        # silently dropped just because the mic/STT side finished (or
        # errored, e.g. connect() itself failing) before translation
        # caught up. Safe to run even if the queue was never touched.
        await translate_queue.put(None)
        await worker_task

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
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass  # client already closed its side


async def translate_and_speak_to_listeners(
    text: str, listeners: list[Participant], client, seq: int, stt_ms, room_id: str, speaker_party_id: str
) -> None:
    """Fan out one committed utterance to every listener, once per distinct
    target language among them (not once per listener -- see module
    docstring). The per-language branches run concurrently so a listener's
    latency doesn't scale with how many other languages are in the room.
    """
    by_language: dict[str, list[Participant]] = {}
    for listener in listeners:
        by_language.setdefault(listener.language, []).append(listener)

    await asyncio.gather(*(
        _speak_one_language(text, target_language, group, client, seq, stt_ms, room_id, speaker_party_id)
        for target_language, group in by_language.items()
    ))


async def _speak_one_language(
    text: str, target_language: str, group: list[Participant],
    client, seq: int, stt_ms, room_id: str, speaker_party_id: str
) -> None:
    t0 = time.perf_counter()
    # source_language_code="auto": the speaker's language is no longer
    # pinned to anything (see module docstring) -- it can vary utterance
    # to utterance, or mix within one, so there's no known-in-advance
    # language to compare against target_language for a same-language
    # shortcut. Mayura detects the source itself from the text.
    translation = await client.text.translate(
        input=text,
        source_language_code="auto",
        target_language_code=target_language,
        model="mayura:v1",
    )
    translated = translation.translated_text
    t1 = time.perf_counter()

    await Session.send_to_json_safe(group, {
        "type": "translated", "speaker": speaker_party_id, "language": target_language, "text": translated, "seq": seq,
    })

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
        "translate_ms": translate_ms,
        "tts_ms": tts_ms,
        "total_ms": total_ms,
    })
    log_metrics({
        "room": room_id,
        "speaker_party": speaker_party_id,
        "listener_parties": listener_ids,
        "source_language": "auto",
        "target_language": target_language,
        "seq": seq,
        "text_chars": len(text),
        "stt_ms": stt_ms,
        "translate_ms": translate_ms,
        "tts_ms": tts_ms,
        "total_ms": total_ms,
    })

    await Session.send_to_json_safe(group, {
        "type": "audio", "speaker": speaker_party_id, "language": target_language, "format": "wav", "data": tts.audios[0], "seq": seq,
    })
