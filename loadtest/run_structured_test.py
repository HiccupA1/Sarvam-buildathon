"""Runs every utterance in test_set.py through the full pipeline and
records real per-stage and end-to-end latency for every language pair.

Each utterance gets its own room with one speaker (streaming that
utterance's audio) and four listeners, one per hearing language
(Hindi, English, Tamil, Telugu). This reuses the same fan-out the app
already does in conference mode, so one STT pass produces four
(source, target) data points per utterance.

end_to_end_ms is measured by this harness, not the server: time from
when the speaker starts sending audio to when a specific listener
receives the translated audio. This is the number the README flagged
as missing (server-side total_ms does not include network transit).

If a listener never receives anything within the timeout, that data
point is recorded as missing (null), not estimated.

Usage: python loadtest/run_structured_test.py
Writes results/structured_test_<timestamp>.json (raw) and
results/structured_test_<timestamp>_summary.csv (aggregated).
"""
import asyncio
import csv
import json
import os
import ssl
import time

import websockets

import test_set

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(SCRIPT_DIR, "test_fixtures")
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")

SERVER_URL = "wss://localhost:8000"
SAMPLE_RATE = 16000
CHUNK_BYTES = 3200
LISTENER_LANGUAGES = ["hi-IN", "en-IN", "ta-IN", "te-IN"]
MAX_CONCURRENT_ROOMS = 1  # 5 participants per room; 4 concurrent rooms (20 connections) timed out in testing
RECEIVE_TIMEOUT_SECONDS = 30

ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE


def load_clip(category, index):
    path = os.path.join(FIXTURES_DIR, f"{category}_{index}.pcm")
    with open(path, "rb") as f:
        return f.read()


async def run_one_utterance(category, index, text):
    room_id = f"structured-{category}-{index}-{int(time.time() * 1000)}"
    clip_bytes = load_clip(category, index)
    results = {}
    send_start_holder = {}

    async def listener_join_then_wait(lang):
        ws = await websockets.connect(f"{SERVER_URL}/ws/{room_id}", ssl=ssl_ctx)
        await ws.send(json.dumps({"type": "join", "language": lang}))
        await ws.recv()
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=RECEIVE_TIMEOUT_SECONDS)
                msg = json.loads(raw)
                if msg["type"] == "audio":
                    received_at = time.perf_counter()
                    send_start = send_start_holder.get("t")
                    end_to_end_ms = round((received_at - send_start) * 1000) if send_start else None
                    entry = results.setdefault(lang, {})
                    entry["end_to_end_ms"] = end_to_end_ms
                    if entry.get("stt_ms") is not None:
                        break
                elif msg["type"] == "latency":
                    entry = results.setdefault(lang, {})
                    entry["stt_ms"] = msg["stt_ms"]
                    entry["translate_ms"] = msg["translate_ms"]
                    entry["tts_ms"] = msg["tts_ms"]
                    entry["total_ms"] = msg["total_ms"]
                    if entry.get("end_to_end_ms") is not None:
                        break
        except asyncio.TimeoutError:
            results.setdefault(lang, None)
        finally:
            try:
                await ws.send(json.dumps({"type": "end"}))
                await ws.close()
            except Exception:
                pass

    async def speaker_join_then_speak():
        ws = await websockets.connect(f"{SERVER_URL}/ws/{room_id}", ssl=ssl_ctx)
        await ws.send(json.dumps({"type": "join", "language": "en-IN"}))
        await ws.recv()
        # Fixed delay rather than a barrier waiting for all 5 to connect:
        # a barrier can make a fast-connecting listener wait on the
        # slowest of 5 (up to ~3.8s, see loadtest/results/ from Priority 1)
        # before the speaker even starts, which matters if idle STT
        # sessions have any kind of timeout.
        await asyncio.sleep(1.0)
        send_start_holder["t"] = time.perf_counter()
        for offset in range(0, len(clip_bytes), CHUNK_BYTES):
            await ws.send(clip_bytes[offset:offset + CHUNK_BYTES])
            await asyncio.sleep(CHUNK_BYTES / (SAMPLE_RATE * 2))
        await ws.send(json.dumps({"type": "end"}))
        try:
            await asyncio.wait_for(ws.recv(), timeout=5)
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            pass
        await ws.close()

    named_tasks = {lang: asyncio.create_task(listener_join_then_wait(lang)) for lang in LISTENER_LANGUAGES}
    named_tasks["speaker"] = asyncio.create_task(speaker_join_then_speak())
    tasks = list(named_tasks.values())
    task_labels = {task: label for label, task in named_tasks.items()}

    # If one task fails (for example the speaker's connection drops),
    # cancel the rest right away instead of letting them run to their own
    # 30 second timeout. An earlier version used plain gather(), which
    # does not cancel sibling tasks on failure -- the leftover listener
    # tasks from one failed utterance kept running in the background
    # while the loop moved on to the next utterance, and this piled up
    # over the run until something broke and every later utterance
    # failed too.
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        if not task.cancelled() and task.exception() is not None:
            label = task_labels[task]
            print(f"  {category}_{index} [{label}] failed: {task.exception()!r}")

    return {
        "category": category,
        "index": index,
        "text": text,
        "room_id": room_id,
        "results": results,
    }


async def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    all_entries = test_set.ALL_STANDARD + test_set.ALL_CODEMIX
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ROOMS)

    async def run_with_limit(category, index, text):
        async with semaphore:
            print(f"running {category}_{index}...")
            try:
                return await run_one_utterance(category, index, text)
            except Exception as e:
                print(f"  {category}_{index} failed: {e!r}")
                return {"category": category, "index": index, "text": text, "room_id": None, "results": {}}

    all_results = await asyncio.gather(*(
        run_with_limit(category, index, text) for category, index, text in all_entries
    ))

    timestamp = int(time.time())
    raw_path = os.path.join(RESULTS_DIR, f"structured_test_{timestamp}.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"wrote {raw_path}")

    write_summary(all_results, os.path.join(RESULTS_DIR, f"structured_test_{timestamp}_summary.csv"))


def write_summary(all_results, out_path):
    lang_name = {"hi-IN": "hi", "en-IN": "en", "ta-IN": "ta", "te-IN": "te"}
    rows = []
    for entry in all_results:
        category = entry["category"]
        for target_lang, data in entry["results"].items():
            rows.append({
                "source": category,
                "target": lang_name.get(target_lang, target_lang),
                "stt_ms": data.get("stt_ms") if data else None,
                "translate_ms": data.get("translate_ms") if data else None,
                "tts_ms": data.get("tts_ms") if data else None,
                "total_ms": data.get("total_ms") if data else None,
                "end_to_end_ms": data.get("end_to_end_ms") if data else None,
            })

    groups = {}
    for row in rows:
        key = (row["source"], row["target"])
        groups.setdefault(key, []).append(row)

    missing_count = sum(1 for row in rows if row["end_to_end_ms"] is None)
    print(f"total data points: {len(rows)}, missing: {missing_count}")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "source", "target", "n", "n_missing",
            "stt_ms_p50", "stt_ms_p95",
            "translate_ms_p50", "translate_ms_p95",
            "tts_ms_p50", "tts_ms_p95",
            "end_to_end_ms_p50", "end_to_end_ms_p95",
        ])

        def percentile(values, p):
            if not values:
                return None
            values = sorted(values)
            index = min(len(values) - 1, int(len(values) * p))
            return values[index]

        for (source, target), group_rows in sorted(groups.items()):
            stt_values = [r["stt_ms"] for r in group_rows if r["stt_ms"] is not None]
            translate_values = [r["translate_ms"] for r in group_rows if r["translate_ms"] is not None]
            tts_values = [r["tts_ms"] for r in group_rows if r["tts_ms"] is not None]
            e2e_values = [r["end_to_end_ms"] for r in group_rows if r["end_to_end_ms"] is not None]
            n_missing = sum(1 for r in group_rows if r["end_to_end_ms"] is None)
            writer.writerow([
                source, target, len(group_rows), n_missing,
                percentile(stt_values, 0.5), percentile(stt_values, 0.95),
                percentile(translate_values, 0.5), percentile(translate_values, 0.95),
                percentile(tts_values, 0.5), percentile(tts_values, 0.95),
                percentile(e2e_values, 0.5), percentile(e2e_values, 0.95),
            ])

        all_e2e = [r["end_to_end_ms"] for r in rows if r["end_to_end_ms"] is not None]
        all_stt = [r["stt_ms"] for r in rows if r["stt_ms"] is not None]
        writer.writerow([])
        writer.writerow(["OVERALL", "", len(rows), missing_count,
                          percentile(all_stt, 0.5), percentile(all_stt, 0.95),
                          "", "", "", "",
                          percentile(all_e2e, 0.5), percentile(all_e2e, 0.95)])

    print(f"wrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
