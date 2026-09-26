"""Load test for conference mode: runs several full 5-person sessions
back to back against the live server, using pre-recorded audio clips
instead of a real microphone, and records the actual per-stage timing
the server reports for each utterance.

Layout per session, fixed across all sessions so any variance we see is
about timing/concurrency, not language-pair differences:
  party A hears Hindi
  party B hears English
  party C hears Tamil, and is the speaker (speaks the Tamil clip)
  party D hears Tamil too, on purpose, to exercise the same-language
    listener group (should be a passthrough, no translate call)
  party E hears Telugu

All 5 join, then C speaks once. This produces 4 distinct target-language
groups (Tamil passthrough for C+D, English, Hindi, Telugu) from a single
utterance, matching the fan-out this feature is built around.

Usage: python loadtest/run_load_test.py [num_sessions]
Writes results/load_test_<timestamp>.json
"""
import asyncio
import json
import os
import ssl
import sys
import time

import websockets

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(SCRIPT_DIR, "fixtures")
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")

SERVER_URL = "wss://localhost:8000"
SAMPLE_RATE = 16000
CHUNK_BYTES = 3200

ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

LAYOUT = [
    ("A", "hi-IN", None),
    ("B", "en-IN", None),
    ("C", "ta-IN", "tamil"),
    ("D", "ta-IN", None),
    ("E", "te-IN", None),
]


def load_clip(name):
    path = os.path.join(FIXTURES_DIR, f"{name}.pcm")
    with open(path, "rb") as f:
        return f.read()


async def run_participant(room_id, label, hearing_language, speak_clip_name, session_result):
    async with websockets.connect(f"{SERVER_URL}/ws/{room_id}", ssl=ssl_ctx) as ws:
        await ws.send(json.dumps({"type": "join", "language": hearing_language}))
        joined = json.loads(await ws.recv())
        party_id = joined["party_id"]

        received_events = []
        connect_done_time = time.perf_counter()

        async def receive_loop():
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    received_events.append((time.perf_counter(), msg))
                    if msg["type"] == "audio":
                        return
            except websockets.exceptions.ConnectionClosed:
                pass

        receive_task = asyncio.create_task(receive_loop())

        if speak_clip_name:
            clip = load_clip(speak_clip_name)
            await asyncio.sleep(1.0)  # let everyone else finish joining first
            send_start_time = time.perf_counter()
            for offset in range(0, len(clip), CHUNK_BYTES):
                await ws.send(clip[offset:offset + CHUNK_BYTES])
                await asyncio.sleep(CHUNK_BYTES / (SAMPLE_RATE * 2))
            await ws.send(json.dumps({"type": "end"}))
            session_result["speaker_send_start"] = send_start_time
            try:
                await asyncio.wait_for(receive_task, timeout=30)
            except asyncio.TimeoutError:
                session_result.setdefault("timeouts", []).append(party_id)
        else:
            try:
                await asyncio.wait_for(receive_task, timeout=30)
            except asyncio.TimeoutError:
                session_result.setdefault("timeouts", []).append(party_id)
            await ws.send(json.dumps({"type": "end"}))

        session_result["connect_times"][party_id] = connect_done_time
        session_result["events"][party_id] = [
            {"t": t, "type": m["type"], "language": m.get("language"), "seq": m.get("seq"),
             "stt_ms": m.get("stt_ms"), "translate_ms": m.get("translate_ms"),
             "tts_ms": m.get("tts_ms"), "total_ms": m.get("total_ms")}
            for t, m in received_events
        ]


async def run_one_session(session_index):
    room_id = f"loadtest-{session_index}-{int(time.time())}"
    session_result = {
        "session_index": session_index,
        "room_id": room_id,
        "started_at": time.time(),
        "connect_times": {},
        "events": {},
    }
    session_start = time.perf_counter()
    await asyncio.gather(*(
        run_participant(room_id, label, lang, speak_clip, session_result)
        for label, lang, speak_clip in LAYOUT
    ))
    session_result["session_wall_seconds"] = round(time.perf_counter() - session_start, 2)
    return session_result


async def main():
    num_sessions = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    os.makedirs(RESULTS_DIR, exist_ok=True)

    all_sessions = []
    for i in range(num_sessions):
        print(f"running session {i + 1}/{num_sessions}...")
        result = await run_one_session(i + 1)
        all_sessions.append(result)
        for party_id, events in result["events"].items():
            for e in events:
                if e["type"] == "latency":
                    print(
                        f"  session {i + 1} {party_id} lang={e['language']} "
                        f"stt_ms={e['stt_ms']} translate_ms={e['translate_ms']} "
                        f"tts_ms={e['tts_ms']} total_ms={e['total_ms']}"
                    )
        if result.get("timeouts"):
            print(f"  WARNING: timeouts in session {i + 1}: {result['timeouts']}")

    out_path = os.path.join(RESULTS_DIR, f"load_test_{int(time.time())}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_sessions, f, indent=2)
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
