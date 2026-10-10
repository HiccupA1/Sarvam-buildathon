"""Checkpointed live benchmark. Defaults to a zero-credit five-clip dry run."""
import argparse
import asyncio
import csv
import json
import os
import ssl
import time
from pathlib import Path

import websockets
import test_set

SCRIPT_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = SCRIPT_DIR / "test_fixtures"
RESULTS_DIR = SCRIPT_DIR / "results"
SAMPLE_RATE = 16000
CHUNK_BYTES = 3200
LISTENER_LANGUAGES = ["hi-IN", "en-IN", "ta-IN", "te-IN"]


def select_entries(per_category, maximum):
    entries = test_set.ALL_STANDARD + test_set.ALL_CODEMIX
    categories = {}
    for category, index, text in entries:
        categories.setdefault(category, []).append((category, index, text))
    selected = []
    for sample in range(per_category):
        for group in categories.values():
            if sample < len(group):
                selected.append(group[sample])
    return selected[:maximum]


def connection_options(url, insecure):
    if not url.startswith("wss://"):
        return {}
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return {"ssl": ctx}


async def run_one_utterance(category, index, text, args):
    room_id = f"structured-{category}-{index}-{time.time_ns()}"
    clip = (FIXTURES_DIR / f"{category}_{index}.pcm").read_bytes()
    results = {lang: {"end_to_end_ms": None} for lang in LISTENER_LANGUAGES}
    sockets = []
    started = None
    transcript = None
    deadline = asyncio.get_running_loop().time() + args.timeout

    async def receive(ws):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError("utterance deadline reached")
        msg = json.loads(await asyncio.wait_for(ws.recv(), remaining))
        if msg.get("type") == "error":
            raise RuntimeError(msg.get("message", "provider error"))
        return msg

    async def connect(language, role):
        ws = await websockets.connect(f"{args.server_url}/ws/{room_id}",
                                      open_timeout=5, close_timeout=1, max_queue=64,
                                      **connection_options(args.server_url, args.insecure))
        sockets.append(ws)
        await ws.send(json.dumps({"type": "join", "language": language, "role": role,
                                  "access_token": os.environ.get("DEMO_ACCESS_TOKEN", "")}))
        while (await receive(ws)).get("type") != "ready":
            pass
        return ws

    async def listen(ws, lang):
        nonlocal transcript
        while True:
            msg = await receive(ws)
            if msg["type"] == "transcript" and msg.get("kind") == "final":
                transcript = msg["text"]
            if msg["type"] == "translated":
                results[lang]["translated_text"] = msg["text"]
            if msg["type"] == "latency":
                for key in ("stt_ms", "queue_ms", "translate_ms", "tts_ms", "total_ms"):
                    results[lang][key] = msg.get(key)
            if msg["type"] == "audio":
                results[lang]["end_to_end_ms"] = round((time.perf_counter() - started) * 1000)
                return

    tasks = []
    error = None
    try:
        # Listeners open no Sarvam connections. All must be ready before speech.
        listeners = []
        for lang in LISTENER_LANGUAGES:
            listeners.append(await connect(lang, "listener"))
        speaker = await connect("en-IN", "speaker")
        started = time.perf_counter()
        tasks = [asyncio.create_task(listen(ws, lang)) for ws, lang in zip(listeners, LISTENER_LANGUAGES)]
        for offset in range(0, len(clip), CHUNK_BYTES):
            if any(task.done() and task.exception() for task in tasks):
                raise RuntimeError("listener failed during speech")
            chunk = clip[offset:offset + CHUNK_BYTES]
            await speaker.send(chunk)
            await asyncio.sleep(len(chunk) / (SAMPLE_RATE * 2))
        # Give VAD an explicit silence boundary before requesting stream end.
        # The fixtures may end immediately after a word with no trailing silence.
        for _ in range(8):
            await speaker.send(bytes(CHUNK_BYTES))
            await asyncio.sleep(.1)
        await speaker.send(json.dumps({"type": "end"}))
        await asyncio.wait_for(asyncio.gather(*tasks), max(0.1, deadline - asyncio.get_running_loop().time()))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for ws in sockets:
            try:
                await ws.send(json.dumps({"type": "end"}))
            except websockets.exceptions.ConnectionClosed:
                pass
            await ws.close()
    return {"category": category, "index": index, "text": text, "transcript": transcript,
            "room_id": room_id, "results": results, "error": error,
            "complete": error is None and all(r.get("end_to_end_ms") is not None for r in results.values())}


def save_checkpoint(entries, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    write_summary(entries, path.with_name(path.stem + "_summary.csv"))


def write_summary(entries, path):
    groups = {}
    metrics = ("stt_ms", "queue_ms", "translate_ms", "tts_ms", "total_ms", "end_to_end_ms")
    for entry in entries:
        for language in LISTENER_LANGUAGES:
            groups.setdefault((entry["category"], language), []).append(entry.get("results", {}).get(language) or {})

    def percentile(values, quantile):
        if not values:
            return None
        values = sorted(values)
        return values[min(len(values) - 1, int((len(values) - 1) * quantile + 0.5))]

    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["source", "target", "n", "n_missing"] +
                        [f"{key}_p{p}" for key in metrics for p in (50, 95)])
        overall = [row for rows in groups.values() for row in rows]
        for (source, target), rows in [*sorted(groups.items()), (("OVERALL", ""), overall)]:
            stats = []
            for key in metrics:
                values = [r[key] for r in rows if r.get(key) is not None]
                stats.extend([percentile(values, .5), percentile(values, .95)])
            writer.writerow([source, target, len(rows), sum(r.get("end_to_end_ms") is None for r in rows), *stats])


def prepare(args):
    entries = select_entries(args.per_category, args.max_utterances)
    output = Path(args.output)
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else []
    completed = {(e["category"], e["index"]) for e in previous if e.get("complete")}
    entries = [entry for entry in entries if (entry[0], entry[1]) not in completed]
    seconds = sum((FIXTURES_DIR / f"{cat}_{idx}.pcm").stat().st_size / 32000 for cat, idx, _ in entries)
    chars = sum(len(text) for _, _, text in entries)
    estimate = seconds / 3600 * 30 + chars * 4 / 10000 * 20 + chars * 4 * 2 / 10000 * 30
    print(json.dumps({"utterances_remaining": len(entries), "input_seconds": round(seconds, 2),
                      "source_chars": chars, "estimated_inr_excl_gst": round(estimate, 2),
                      "tts_output_assumption": "2x source characters per target; estimate, not billing cap",
                      "live": args.live}, indent=2))
    if seconds > args.max_input_seconds or chars > args.max_source_chars:
        raise SystemExit("Plan exceeds input budget. Choose a smaller sample or explicitly increase limits.")
    if not args.live:
        print("Dry run only: no API calls. Pass --live to execute.")
        return
    if not os.environ.get("DEMO_ACCESS_TOKEN"):
        raise SystemExit("Set DEMO_ACCESS_TOKEN to the server invite before running live tests")
    return entries, previous, output


async def main(args, plan):
    entries, previous, output = plan
    for category, index, text in entries:
        utterance_started = time.monotonic()
        print(f"Running {category}_{index}", flush=True)
        result = await run_one_utterance(category, index, text, args)
        prior = next((e for e in previous if (e["category"], e["index"]) == (category, index)), None)
        if prior:
            result["prior_attempts"] = [*prior.get("prior_attempts", []),
                                        {"room_id": prior.get("room_id"), "error": prior.get("error"),
                                         "complete": prior.get("complete"), "results": prior.get("results")}]
        previous = [e for e in previous if (e["category"], e["index"]) != (category, index)]
        previous.append(result)
        save_checkpoint(previous, output)
        if not result["complete"]:
            raise SystemExit(f"Stopped after failure; checkpoint saved: {result['error']}")
        await asyncio.sleep(max(0, args.min_utterance_seconds - (time.monotonic() - utterance_started)))
    print(f"Saved {output} and summary CSV")


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true")
    p.add_argument("--server-url", default="ws://127.0.0.1:8000")
    p.add_argument("--insecure", action="store_true", help="Local self-signed TLS only")
    p.add_argument("--per-category", type=int, default=1)
    p.add_argument("--max-utterances", type=int, default=5)
    p.add_argument("--max-input-seconds", type=float, default=60)
    p.add_argument("--max-source-chars", type=int, default=2000)
    p.add_argument("--timeout", type=float, default=40)
    p.add_argument("--min-utterance-seconds", type=float, default=0, help="Pace large campaigns below provider rate limits")
    p.add_argument("--output", default=str(RESULTS_DIR / "structured_test_pilot.json"))
    return p


if __name__ == "__main__":
    args = parser().parse_args()
    if args.per_category < 1 or args.max_utterances < 1 or args.timeout <= 0:
        raise SystemExit("Sample limits and timeout must be positive")
    plan = prepare(args)
    if plan is not None:
        asyncio.run(main(args, plan))
