"""Explicitly approved, short real-provider test over local loopback only."""
import argparse
import asyncio
import json
import time
import urllib.request
from pathlib import Path

import websockets
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


async def run():
    endpoint = "ws://127.0.0.1:8780/ws/approved-overlap-check"
    origin = "http://127.0.0.1:8780"
    with urllib.request.urlopen(origin + "/health", timeout=5) as response:
        if json.load(response)["active_rooms"]:
            raise RuntimeError("Stop other demo sessions before this test")
    invite = dotenv_values(ROOT / "logs/local-demo/backend.env")["DEMO_ACCESS_TOKEN"]
    fixtures = [(ROOT / "loadtest/test_fixtures" / f"en_{i}.pcm").read_bytes() for i in (0, 2)]
    if any(len(pcm) / 32000 > 8 for pcm in fixtures):
        raise RuntimeError("Fixture longer than the approved short test")
    events = []
    ready = [asyncio.Event() for _ in range(3)]
    sockets = []
    tasks = []
    started = time.monotonic()

    async def receive(index, ws):
        async for raw in ws:
            msg = json.loads(raw)
            if msg["type"] == "ready":
                ready[index].set()
            if msg["type"] in ("joined", "transcript", "translated", "audio", "error"):
                record = {key: value for key, value in msg.items() if key not in ("data", "resume_token")}
                events.append({"client": index, "elapsed_s": round(time.monotonic() - started, 3), **record})
            if msg["type"] == "error":
                raise RuntimeError(msg.get("message", "Provider error"))

    async def send(pcm, ws):
        for offset in range(0, len(pcm), 3200):
            await ws.send(pcm[offset:offset + 3200])
            await asyncio.sleep(.1)
        # Continue silence for VAD commit and translation completion.
        while True:
            await ws.send(bytes(3200))
            await asyncio.sleep(.1)

    try:
        for index in range(3):
            ws = await websockets.connect(endpoint, origin=origin, max_size=2**22)
            sockets.append(ws)
            await ws.send(json.dumps({"type": "join", "role": "listener" if index == 0 else "speaker",
                                      "language": "hi-IN", "access_token": invite}))
            tasks.append(asyncio.create_task(receive(index, ws)))
            await asyncio.wait_for(ready[index].wait(), 15)
        tasks.extend(asyncio.create_task(send(pcm, ws)) for pcm, ws in zip(fixtures, sockets[1:]))
        async with asyncio.timeout(35):
            while len({event.get("speaker") for event in events if event["client"] == 0 and event["type"] == "audio"}) < 2:
                for task in tasks:
                    if task.done() and not task.cancelled() and task.exception():
                        raise task.exception()
                await asyncio.sleep(.1)
        print("PASS: listener received real translated WAV audio from both overlapping speakers.")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for ws in sockets:
            try:
                await ws.send(json.dumps({"type": "end"}))
            finally:
                await ws.close()
        output = ROOT / "logs/local-demo/live-overlap-result.json"
        output.write_text(json.dumps({"fixture_ids": ["en_0", "en_2"], "events": events,
                                      "elapsed_s": round(time.monotonic() - started, 3)}, indent=2), encoding="utf-8")
        print(f"Evidence: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Requires explicit Sarvam spending approval")
    if not parser.parse_args().live:
        parser.error("--live required; this test reserves two 60-second STT sessions and pays for translation/TTS")
    asyncio.run(run())
