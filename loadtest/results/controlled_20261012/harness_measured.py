"""Reproducible paired real-provider benchmark; dry plan unless --live."""
import argparse
import array
import asyncio
import base64
import hashlib
import io
import json
import math
import os
import platform
import random
import secrets
import struct
import subprocess
import sys
import time
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path

import websockets
from dotenv import dotenv_values

import test_set

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "loadtest/test_fixtures"
DEFAULT_OUTPUT = ROOT / "loadtest/results/controlled_20261012"
RUNTIME = ROOT / "logs/controlled-benchmark"
CONTAINER = "sarvam-controlled-benchmark"
LANGUAGES = ["en-IN", "hi-IN", "ta-IN", "te-IN"]
SEED = 20261012
RATE = 32000


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def fixture_info(category, index, text):
    raw = (FIXTURES / f"{category}_{index}.pcm").read_bytes()
    if raw[:4] == b"RIFF" or len(raw) % 2:
        raise ValueError("Fixtures must be raw PCM16")
    samples = array.array("h", raw)
    if sys.byteorder != "little":
        samples.byteswap()
    audible = [i for i, value in enumerate(samples) if abs(value) > 400]
    if not audible:
        raise ValueError("Silent fixture")
    return {"id": f"{category}_{index}", "category": category, "index": index,
            "reference": text, "sha256": digest(raw), "bytes": len(raw),
            "duration_s": len(raw) / RATE, "signal_start_s": audible[0] / 16000,
            "signal_end_s": (audible[-1] + 1) / 16000,
            "peak": max(abs(v) for v in samples),
            "rms": math.sqrt(sum(v * v for v in samples) / len(samples))}


def make_plan(per_category=6, repetitions=3):
    rng = random.Random(SEED)
    dataset = []
    for category in ("en", "hi", "ta", "te", "mix"):
        entries = [entry for entry in test_set.ALL_STANDARD + test_set.ALL_CODEMIX if entry[0] == category]
        for entry in sorted(rng.sample(entries, per_category), key=lambda item: item[1]):
            dataset.append(fixture_info(*entry))
    shuffled = dataset.copy()
    rng.shuffle(shuffled)
    if len(shuffled) % 2:
        raise ValueError("Dataset size must be even")
    pairs = [shuffled[i:i + 2] for i in range(0, len(shuffled), 2)]
    trials = []
    for repetition in range(repetitions):
        order = list(range(len(pairs)))
        rng.shuffle(order)
        for pair in order:
            conditions = ["alternating", "overlap"]
            if (pair + repetition) % 2:
                conditions.reverse()
            targets = [LANGUAGES[(pair + j) % 4] for j in range(3)]
            for condition in conditions:
                trials.append({"id": f"r{repetition + 1}-p{pair + 1:02d}-{condition}",
                               "repetition": repetition + 1, "pair": pair + 1,
                               "condition": condition, "inputs": pairs[pair], "targets": targets})
    return {"protocol": "controlled-v1", "seed": SEED, "dataset": dataset,
            "repetitions": repetitions, "trials": trials,
            "expected_input_runs": len(trials) * 2,
            "expected_stable_listener_input_results": len(trials) * 6}


def validate_wav(encoded):
    raw = base64.b64decode(encoded, validate=True)
    with wave.open(io.BytesIO(raw), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise ValueError("Unsupported WAV encoding")
        frames = audio.readframes(audio.getnframes())
        values = array.array("h", frames)
        if sys.byteorder != "little":
            values.byteswap()
        if not values or not any(values):
            raise ValueError("Empty/silent WAV")
        return raw, {"wav_duration_s": audio.getnframes() / audio.getframerate(),
                     "wav_rate": audio.getframerate(), "wav_channels": audio.getnchannels(),
                     "wav_bytes": len(raw), "wav_sha256": digest(raw), "wav_valid": True}


async def run_trial(spec, invite, args):
    room = "bench-" + spec["id"]
    began = time.perf_counter()
    events, sockets, tasks = [], [], []
    parties, send_times, frames_sent = {}, {}, {}
    drain = {i: {} for i in range(3)}
    error = None
    cleaning = False

    def record(receiver, msg):
        event = {"receiver": receiver, "at_ms": round((time.perf_counter() - began) * 1000, 3),
                 **{key: value for key, value in msg.items() if key not in ("data", "resume_token", "access_token")}}
        if msg.get("type") == "audio":
            try:
                raw, details = validate_wav(msg["data"])
                event.update(details)
                if receiver < 3:
                    path = RUNTIME / "audio" / spec["id"] / f"l{receiver}-{msg['speaker']}-{msg['seq']}.wav"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if path.exists():
                        event["duplicate_audio_file"] = True
                    else:
                        path.write_bytes(raw)
            except (ValueError, wave.Error, EOFError, KeyError) as exc:
                event.update(wav_valid=False, wav_error=str(exc))
        events.append(event)
        if receiver < 3 and msg.get("type") == "stream_drained":
            drain[receiver][msg["party_id"]] = event

    async def connect(receiver, language, role):
        ws = await websockets.connect(f"ws://127.0.0.1:8782/ws/{room}",
                                      origin="http://127.0.0.1:8782", max_size=2**23,
                                      max_queue=256, close_timeout=2, open_timeout=10)
        sockets.append(ws)
        await ws.send(json.dumps({"type": "join", "role": role, "language": language,
                                  "access_token": invite, "audio_protocol": "timestamped_pcm16"}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
            record(receiver, msg)
            if msg["type"] == "error":
                raise RuntimeError(msg["message"])
            if msg["type"] == "joined":
                parties[receiver] = msg["party_id"]
            if msg["type"] == "ready":
                return ws

    async def read(receiver, ws):
        async for raw in ws:
            record(receiver, json.loads(raw))
        if receiver < 3 and not cleaning:
            raise RuntimeError("Stable listener disconnected before cleanup")

    async def send(slot, ws, clip, offset, anchor, finish):
        await asyncio.sleep(max(0, anchor + offset - time.perf_counter()))
        start = time.perf_counter()
        send_times[slot] = (start - began) * 1000
        raw = (FIXTURES / (clip["id"] + ".pcm")).read_bytes()
        frames_sent[slot] = {"bytes": 0, "late_frames": 0, "max_lateness_ms": 0}
        frame = 0
        while time.perf_counter() < finish:
            pcm = raw[frame * 3200:(frame + 1) * 3200] if frame * 3200 < len(raw) else b""
            pcm = pcm.ljust(3200, b"\0")
            await ws.send(struct.pack("<d", time.time() * 1000) + pcm)
            frames_sent[slot]["bytes"] += len(pcm)
            frame += 1
            scheduled = start + frame * .1
            late = max(0, time.perf_counter() - scheduled) * 1000
            frames_sent[slot]["late_frames"] += int(late > 20)
            frames_sent[slot]["max_lateness_ms"] = max(frames_sent[slot]["max_lateness_ms"], late)
            await asyncio.sleep(max(0, scheduled - time.perf_counter()))
        await ws.send(json.dumps({"type": "end"}))

    try:
        async with asyncio.timeout(args.timeout):
            for index, lang in enumerate(spec["targets"]):
                await connect(index, lang, "listener")
            for index in (3, 4):
                await connect(index, spec["targets"][0], "speaker")
            tasks = [asyncio.create_task(read(i, ws)) for i, ws in enumerate(sockets)]
            anchor = time.perf_counter()
            offset = .3 if spec["condition"] == "overlap" else spec["inputs"][0]["duration_s"] + 1
            finish = anchor + max(spec["inputs"][0]["duration_s"], offset + spec["inputs"][1]["duration_s"]) + 1.2
            tasks.extend([asyncio.create_task(send(i, sockets[i], spec["inputs"][i - 3], start_offset, anchor, finish))
                          for i, start_offset in ((3, 0), (4, offset))])
            while not all(set(drain[i]) >= {parties[3], parties[4]} for i in range(3)):
                for task in tasks:
                    if task.done() and not task.cancelled() and task.exception():
                        raise task.exception()
                await asyncio.sleep(.02)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        cleaning = True
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for ws in sockets:
            try:
                await ws.send(json.dumps({"type": "end"}))
            except websockets.exceptions.ConnectionClosed:
                pass
            await ws.close()
    event_path = Path(args.output) / "events" / (spec["id"] + ".jsonl")
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    return {"spec": spec, "room": room, "parties": parties, "send_start_ms": send_times,
            "frames_sent": frames_sent, "duration_s": round(time.perf_counter() - began, 3),
            "error": error, "events_file": str(event_path.relative_to(Path(args.output))),
            "event_count": len(events), "events_sha256": digest(event_path.read_bytes())}


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result


def start_backend():
    if docker("inspect", CONTAINER, check=False).returncode == 0:
        raise RuntimeError("Benchmark container already exists; inspect it before proceeding")
    key = os.getenv("SARVAM_API_KEY") or dotenv_values(ROOT / ".env").get("SARVAM_API_KEY")
    if not key or "\n" in key or "\r" in key:
        raise RuntimeError("Valid local Sarvam key required")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    invite = secrets.token_urlsafe(32)
    environment = {"SARVAM_API_KEY": key, "DEMO_ACCESS_TOKEN": invite, "LIVE_ENABLED": "true",
                   "WEB_CONCURRENCY": "1", "MAX_ROOMS": "1", "SESSION_SECONDS": "60",
                   "MAX_STT_SECONDS": "unlimited", "MAX_TRANSLATE_CHARS": "unlimited",
                   "MAX_TTS_CHARS": "unlimited", "USAGE_DB_PATH": "/app/logs/usage.sqlite3",
                   "ALLOWED_ORIGINS": "http://127.0.0.1:8782"}
    env_path = RUNTIME / "backend.env"
    env_path.write_text("".join(f"{key}={value}\n" for key, value in environment.items()))
    if os.name != "nt":
        env_path.chmod(0o600)
    try:
        docker("run", "-d", "--name", CONTAINER, "--label", "sarvam.controlled-benchmark=true",
               "--env-file", str(env_path), "-p", "127.0.0.1:8782:8000",
               "--mount", f"type=bind,source={RUNTIME},target=/app/logs", "sarvam-interpreter:controlled-benchmark")
        for _ in range(40):
            try:
                with urllib.request.urlopen("http://127.0.0.1:8782/health", timeout=2) as response:
                    if json.load(response)["status"] == "ok":
                        return invite
            except OSError:
                pass
            time.sleep(.5)
        raise RuntimeError("Benchmark backend did not start")
    finally:
        env_path.unlink(missing_ok=True)


def stop_backend():
    inspected = docker("inspect", CONTAINER, check=False)
    if inspected.returncode:
        return
    item = json.loads(inspected.stdout)[0]
    if item["Config"].get("Labels", {}).get("sarvam.controlled-benchmark") != "true":
        raise RuntimeError("Refusing to stop unrelated container")
    docker("stop", CONTAINER)
    docker("rm", CONTAINER)


async def campaign(args, plan, invite):
    output = Path(args.output)
    warmup = dict(plan["trials"][0], id="diagnostic-alternating", condition="alternating")
    for condition in ("alternating", "overlap"):
        path = output / "trials" / f"diagnostic-{condition}.json"
        if not path.exists():
            spec = dict(warmup, id="diagnostic-" + condition, condition=condition)
            result = await run_trial(spec, invite, args)
            save_json(path, result)
            if result["error"]:
                raise RuntimeError("Diagnostic failed: " + result["error"])
    for number, spec in enumerate(plan["trials"], 1):
        path = output / "trials" / (spec["id"] + ".json")
        if path.exists():
            continue
        started = time.monotonic()
        result = await run_trial(spec, invite, args)
        save_json(path, result)
        print(f"{number}/{len(plan['trials'])} {spec['id']} {result['duration_s']}s "
              f"events={result['event_count']} error={result['error']}", flush=True)
        await asyncio.sleep(max(0, args.min_trial_seconds - (time.monotonic() - started)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--per-category", type=int, default=6)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--min-trial-seconds", type=float, default=12)
    args = parser.parse_args()
    if not 1 <= args.per_category <= 12 or args.repetitions < 1 or args.timeout <= 0:
        parser.error("Invalid positive limits")
    plan = make_plan(args.per_category, args.repetitions)
    output = Path(args.output)
    manifest_path = output / "manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding="utf-8")) != plan:
        raise RuntimeError("Do not mix different protocols in one output directory")
    save_json(manifest_path, plan)
    print(json.dumps({"trials": len(plan["trials"]), "input_runs": plan["expected_input_runs"],
                      "stable_listener_results": plan["expected_stable_listener_input_results"],
                      "reused_unique_fixtures": len(plan["dataset"]), "live": args.live}, indent=2))
    if not args.live:
        print("Plan only: no API calls")
        return
    metadata_path = output / "metadata.json"
    backend_hash = digest((ROOT / "backend/app.py").read_bytes())
    harness_hash = digest(Path(__file__).read_bytes())
    image = docker("image", "inspect", "--format", "{{.Id}}", "sarvam-interpreter:controlled-benchmark").stdout.strip()
    metadata = {"protocol_date_local": "2026-10-12", "started_utc": datetime.now(timezone.utc).isoformat(),
                "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "backend_sha256": backend_hash, "harness_sha256": harness_hash, "docker_image_id": image,
                "host": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
                "transport": "local loopback WebSocket", "workers": 1, "timeout_s": args.timeout,
                "min_trial_seconds": args.min_trial_seconds, "status": "running", "endurance_test": False}
    if metadata_path.exists():
        previous = json.loads(metadata_path.read_text())
        if any(previous[key] != metadata[key] for key in ("backend_sha256", "harness_sha256", "docker_image_id")):
            raise RuntimeError("Cannot resume campaign with a different build")
        metadata = previous
    save_json(metadata_path, metadata)
    try:
        invite = start_backend()
        asyncio.run(campaign(args, plan, invite))
        metadata["status"] = "completed"
    finally:
        stop_backend()
        metadata["ended_utc"] = datetime.now(timezone.utc).isoformat()
        save_json(metadata_path, metadata)


if __name__ == "__main__":
    main()
