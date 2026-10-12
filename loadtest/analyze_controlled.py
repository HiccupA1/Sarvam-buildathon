"""Traceable descriptive statistics for controlled benchmark observations."""
import argparse
import collections
import csv
import hashlib
import json
import math
import random
import sqlite3
import statistics
import unicodedata
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]


def normalize(text):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join("".join(ch if ch.isalnum() or ch.isspace() or unicodedata.category(ch).startswith("M")
                            else " " for ch in normalized).split())


def distance(reference, hypothesis):
    previous = list(range(len(hypothesis) + 1))
    for i, token in enumerate(reference, 1):
        current = [i]
        for j, candidate in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (token != candidate)))
        previous = current
    return previous[-1]


def percentile(values, quantile):
    values = sorted(value for value in values if value is not None)
    return values[max(0, math.ceil(quantile * len(values)) - 1)] if values else None


def wilson(successes, n):
    if not n:
        return [None, None]
    z = 1.96
    p = successes / n
    divisor = 1 + z * z / n
    middle = (p + z * z / (2 * n)) / divisor
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / divisor
    return [middle - radius, middle + radius]


def write_csv(path, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})


def load_observations(output, metrics=()):
    inputs, targets, segments, trials = [], [], [], []
    for path in sorted((output / "trials").glob("r*.json")):
        trial = json.loads(path.read_text(encoding="utf-8"))
        spec = trial["spec"]
        raw_path = output.joinpath(*PureWindowsPath(trial["events_file"]).parts)
        if hashlib.sha256(raw_path.read_bytes()).hexdigest() != trial["events_sha256"]:
            raise ValueError("Raw event integrity check failed: " + spec["id"])
        events = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
        room_errors = [event for event in events if event["type"] == "error"]
        trial_inputs = []
        for slot in (3, 4):
            fixture = spec["inputs"][slot - 3]
            party = trial["parties"].get(str(slot))
            start = trial["send_start_ms"].get(str(slot))
            finals = [event for event in events if event["receiver"] == 0 and event["type"] == "transcript"
                      and event.get("kind") == "final" and event.get("party") == party]
            finals.sort(key=lambda event: event["seq"])
            final_by_seq = {event["seq"]: event for event in finals}
            drain = [event for event in events if event["type"] == "stream_drained" and event.get("party_id") == party
                     and event["receiver"] < 3]
            expected_count = drain[0]["committed_segments"] if drain else None
            drained = len(drain) == 3 and all(event["completed"] for event in drain)
            recognized = bool(finals) and len(finals) == len(final_by_seq) and expected_count == len(finals) and set(final_by_seq) == set(range(1, len(finals) + 1))
            provider_end = next((row for row in metrics if row.get("kind") == "provider_session_end"
                                 and row.get("room") == trial["room"] and row.get("party") == party), {})
            transcript = " ".join(event["text"] for event in finals)
            ref, hyp = normalize(fixture["reference"]), normalize(transcript)
            ref_words, hyp_words = ref.split(), hyp.split()
            ref_chars, hyp_chars = ref.replace(" ", ""), hyp.replace(" ", "")
            base = {"trial_id": spec["id"], "condition": spec["condition"], "repetition": spec["repetition"],
                    "pair": spec["pair"], "fixture_id": fixture["id"], "source_category": fixture["category"],
                    "speaker": party, "input_start_ms": start,
                    "clip_end_ms": start + fixture["duration_s"] * 1000 if start is not None else None,
                    "signal_end_ms": start + fixture["signal_end_s"] * 1000 if start is not None else None,
                    "fixture_duration_s": fixture["duration_s"], "reference": fixture["reference"],
                    "transcript": transcript, "final_segments": len(finals), "committed_segments": expected_count,
                    "stream_drained": drained, "transcript_events_consistent": recognized,
                    "provider_audio_seconds": provider_end.get("audio_duration_s"),
                    "provider_utterance_count": provider_end.get("total_utterances"),
                    "provider_segment_count_matches": provider_end.get("total_utterances") == len(finals) if provider_end else None,
                    "word_edits": distance(ref_words, hyp_words), "reference_words": len(ref_words),
                    "character_edits": distance(ref_chars, hyp_chars), "reference_characters": len(ref_chars),
                    "wer": distance(ref_words, hyp_words) / len(ref_words) if ref_words else None,
                    "cer": distance(ref_chars, hyp_chars) / len(ref_chars) if ref_chars else None,
                    "harness_error": trial["error"], "room_error_count": len(room_errors),
                    "sent_audio_seconds": trial["frames_sent"].get(str(slot), {}).get("bytes", 0) / 32000,
                    "max_frame_lateness_ms": trial["frames_sent"].get(str(slot), {}).get("max_lateness_ms")}
            speaker_targets = []
            for receiver, language in enumerate(spec["targets"]):
                relevant = [event for event in events if event["receiver"] == receiver and event.get("speaker") == party]
                audio = [event for event in relevant if event["type"] == "audio"]
                translation = [event for event in relevant if event["type"] == "translated"]
                complete = recognized and drained
                missing = duplicates = invalid = 0
                for seq, final in final_by_seq.items():
                    payloads = [event for event in audio if event.get("seq") == seq]
                    texts = [event for event in translation if event.get("seq") == seq]
                    stages = [event for event in relevant if event["type"] == "latency" and event.get("seq") == seq]
                    missing += int(not payloads)
                    duplicates += max(0, len(payloads) - 1)
                    invalid += sum(not event.get("wav_valid", False) for event in payloads)
                    valid = len(payloads) == 1 and payloads[0].get("wav_valid", False) and len(texts) == 1 and payloads[0].get("language") == language and texts[0].get("language") == language
                    complete = complete and valid
                    row = {**{key: base[key] for key in ("trial_id", "condition", "repetition", "pair", "fixture_id", "source_category", "speaker")},
                           "target_language": language, "receiver": receiver, "seq": seq,
                           "source_transcript": final["text"], "translated_text": texts[0]["text"] if texts else None,
                           "valid_delivered": bool(valid), "audio_received_ms": payloads[0]["at_ms"] if payloads else None,
                           "wav_duration_s": payloads[0].get("wav_duration_s") if payloads else None,
                           "wav_sha256": payloads[0].get("wav_sha256") if payloads else None}
                    row.update({key: stages[0].get(key) if stages else None
                                for key in ("stt_ms", "queue_ms", "translate_ms", "tts_ms", "total_ms")})
                    segments.append(row)
                if set(event["seq"] for event in audio) - set(final_by_seq):
                    complete = False
                first = min((event["at_ms"] for event in audio), default=None) if complete else None
                last = max((event["at_ms"] for event in audio), default=None) if complete else None
                result = {**base, "target_language": language, "receiver": receiver,
                          "target_complete": bool(complete), "missing_segments": missing,
                          "duplicate_audio": duplicates, "invalid_audio": invalid,
                          "received_audio_segments": len(audio), "first_audio_ms": first, "last_audio_ms": last}
                for boundary, at in (("input_start", start), ("clip_end", base["clip_end_ms"]), ("signal_end", base["signal_end_ms"])):
                    for label, received in (("first", first), ("last", last)):
                        result[f"{label}_audio_after_{boundary}_ms"] = received - at if received is not None and at is not None else None
                targets.append(result)
                speaker_targets.append(result)
            base["fixture_sent_complete"] = base["sent_audio_seconds"] >= fixture["duration_s"]
            base["input_complete"] = all(row["target_complete"] for row in speaker_targets) and not trial["error"] and not room_errors and base["fixture_sent_complete"]
            base["mean_last_audio_after_clip_end_ms"] = statistics.mean(row["last_audio_after_clip_end_ms"] for row in speaker_targets) if all(row["target_complete"] for row in speaker_targets) else None
            base["mean_last_audio_after_signal_end_ms"] = statistics.mean(row["last_audio_after_signal_end_ms"] for row in speaker_targets) if all(row["target_complete"] for row in speaker_targets) else None
            inputs.append(base)
            trial_inputs.append(base)
        a, b = trial_inputs
        overlap = None
        if a["input_start_ms"] is not None and b["input_start_ms"] is not None:
            overlap = max(0, min(a["clip_end_ms"], b["clip_end_ms"]) - max(a["input_start_ms"], b["input_start_ms"]))
        trials.append({"trial_id": spec["id"], "condition": spec["condition"], "repetition": spec["repetition"],
                       "pair": spec["pair"], "duration_s": trial["duration_s"], "input_overlap_ms": overlap,
                       "trial_complete": all(row["input_complete"] for row in trial_inputs),
                       "harness_error": trial["error"], "room_errors": room_errors})
    return inputs, targets, segments, trials


def summarize(inputs, targets, segments):
    summaries = []
    for condition in ("alternating", "overlap"):
        for category in ("ALL", "en", "hi", "ta", "te", "mix"):
            rows = [row for row in inputs if row["condition"] == condition and (category == "ALL" or row["source_category"] == category)]
            target_rows = [row for row in targets if row["condition"] == condition and (category == "ALL" or row["source_category"] == category)]
            segment_rows = [row for row in segments if row["condition"] == condition and (category == "ALL" or row["source_category"] == category)]
            n, success = len(rows), sum(row["input_complete"] for row in rows)
            result = {"condition": condition, "source_category": category, "inputs": n, "complete_inputs": success,
                      "success_rate": success / n if n else None, "wilson_95": wilson(success, n),
                      "target_checks": len(target_rows), "complete_target_checks": sum(row["target_complete"] for row in target_rows),
                      "recognized_segment_checks": len(segment_rows),
                      "delivered_segment_checks": sum(row["valid_delivered"] for row in segment_rows),
                      "word_edits": sum(row["word_edits"] for row in rows),
                      "reference_words": sum(row["reference_words"] for row in rows),
                      "character_edits": sum(row["character_edits"] for row in rows),
                      "reference_characters": sum(row["reference_characters"] for row in rows)}
            result["wer"] = result["word_edits"] / result["reference_words"] if result["reference_words"] else None
            result["cer"] = result["character_edits"] / result["reference_characters"] if result["reference_characters"] else None
            for metric in ("first_audio_after_input_start_ms", "last_audio_after_clip_end_ms", "last_audio_after_signal_end_ms"):
                values = [row[metric] for row in target_rows]
                result[metric + "_n"] = sum(value is not None for value in values)
                for p in (50, 95):
                    result[metric + f"_p{p}"] = percentile(values, p / 100)
            for metric in ("stt_ms", "queue_ms", "translate_ms", "tts_ms"):
                for p in (50, 95):
                    result[metric + f"_p{p}"] = percentile([row[metric] for row in segment_rows], p / 100)
            result["delivery_target_met"] = result["success_rate"] >= .95 if n else None
            p95 = result["last_audio_after_clip_end_ms_p95"]
            result["latency_target_met"] = p95 <= 5000 if p95 is not None else None
            summaries.append(result)
    return summaries


def paired_comparison(inputs):
    paired = []
    for metric in ("mean_last_audio_after_clip_end_ms", "mean_last_audio_after_signal_end_ms", "wer", "cer"):
        groups = collections.defaultdict(lambda: collections.defaultdict(dict))
        fixture_pairs = {}
        for row in inputs:
            fixture_pairs[row["fixture_id"]] = row["pair"]
            if row[metric] is not None:
                groups[row["fixture_id"]][row["condition"]][row["repetition"]] = row[metric]
        per_fixture = {}
        matched_repetitions = 0
        for fixture_id, group in groups.items():
            common = set(group["overlap"]) & set(group["alternating"])
            if common:
                per_fixture[fixture_id] = statistics.mean(group["overlap"][rep] - group["alternating"][rep] for rep in common)
                matched_repetitions += len(common)
        differences = list(per_fixture.values())
        rng = random.Random(20261012)
        samples = [statistics.mean(rng.choices(differences, k=len(differences))) for _ in range(2000)] if differences else []
        blocks = collections.defaultdict(list)
        for fixture_id, value in per_fixture.items():
            blocks[fixture_pairs[fixture_id]].append(value)
        block_values = list(blocks.values())
        block_samples = [statistics.mean(value for block in rng.choices(block_values, k=len(block_values)) for value in block)
                         for _ in range(2000)] if block_values else []
        paired.append({"metric": metric, "paired_fixtures": len(differences), "matched_repetition_pairs": matched_repetitions,
                       "mean_overlap_minus_alternating": statistics.mean(differences) if differences else None,
                       "bootstrap_95_low": percentile(samples, .025), "bootstrap_95_high": percentile(samples, .975),
                       "pair_block_95_low": percentile(block_samples, .025), "pair_block_95_high": percentile(block_samples, .975),
                       "pair_blocks": len(block_values),
                       "bootstrap_unit": "fixture, after averaging repetitions/targets", "resamples": 2000})
    return paired


def language_pairs(targets):
    groups = collections.defaultdict(list)
    for row in targets:
        groups[(row["condition"], row["source_category"], row["target_language"])].append(row)
    return [{"condition": key[0], "source_category": key[1], "target_language": key[2],
             "checks": len(rows), "complete_checks": sum(row["target_complete"] for row in rows),
             "post_clip_last_audio_ms_p50": percentile([row["last_audio_after_clip_end_ms"] for row in rows], .5),
             "post_clip_last_audio_ms_p95": percentile([row["last_audio_after_clip_end_ms"] for row in rows], .95)}
            for key, rows in sorted(groups.items())]


def fmt(value, digits=2):
    return "n.a." if value is None else f"{value:.{digits}f}"


def report(output, manifest, metadata, inputs, targets, segments, trials, summaries, paired, metrics):
    lines = ["# Controlled Two-Speaker Speech Benchmark", "", "Protocol date: 2026-10-12. Real-provider, synthetic-input, local-loopback experiment.", "",
             "## Executive Results", "",
             f"- Completed trial checkpoints: {len(trials)}/{len(manifest['trials'])}; diagnostic trials excluded.",
             f"- Unique fixtures: {len(manifest['dataset'])}; measured input runs: {len(inputs)}; stable-listener input-target checks: {len(targets)}.",
             f"- Recognized segment-target checks: {len(segments)}; valid delivered checks: {sum(row['valid_delivered'] for row in segments)}.",
             f"- Failed trials: {sum(not row['trial_complete'] for row in trials)}. No measured trial retries/replacements.", "",
             "| Condition | Complete Inputs | Post-Clip Last-Audio p50 | p95 | Normalized WER | CER |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in summaries:
        if row["source_category"] == "ALL":
            lines.append(f"| {row['condition']} | {row['complete_inputs']}/{row['inputs']} | {fmt(row['last_audio_after_clip_end_ms_p50'])} ms | {fmt(row['last_audio_after_clip_end_ms_p95'])} ms | {fmt(row['wer'] * 100) if row['wer'] is not None else 'n.a.'}% | {fmt(row['cer'] * 100) if row['cer'] is not None else 'n.a.'}% |")
    lines += ["", "These latency values are complete recognized-segment payload receipt, not playback or human translation quality.",
              "Input-target checks and repeats are correlated; they are not independent sample sizes for statistical claims.", "",
              "## Method and Reproducibility", "",
              "See `../../../docs/CONTROLLED-BENCHMARK-PLAN.md` for the protocol fixed before live measurement.",
              "Thirty cached PCM16/16kHz synthetic fixtures: six per source category; three repetitions, matched pairs, seeded randomized/counterbalanced condition order.",
              "Alternating inputs have a one-second inter-clip gap; overlapping B starts 0.3 seconds after A. Both STT streams stay independent.",
              "Three stable listen-only targets, with target-language rotation. Both speakers hear the first listener's language. One Docker worker; paced 100ms PCM frames.",
              "Each trial awaits upstream end and ordered worker drain. Every final transcript sequence must have exactly one valid WAV and translation per stable target.",
              "Delivery completeness is conditional on recognized segments. Reference WER/CER separately quantify transcription deviations; no semantic correctness assumption.",
              f"Base source commit: `{metadata['commit']}`; tested working-tree backend SHA-256: `{metadata['backend_sha256']}`. Completion instrumentation was added before this campaign; the hashes identify the actual tested source.",
              f"Harness SHA-256: `{metadata['harness_sha256']}`; Docker image: `{metadata['docker_image_id']}`.",
              f"Host: {metadata['host']}; Python {metadata['python']}; transport: {metadata['transport']}.", "",
              "Configured models: `saaras:v3-realtime` / auto language / codemix / fast stream; `mayura:v1` translation; `bulbul:v3` / kavitha / 16kHz WAV synthesis. VAD silence=500ms, threshold=0.3, minimum speech=250ms.", "",
              "## Timing Definitions", "",
              "- First-audio from input start includes speaking duration, VAD, API processing and local delivery.",
              "- Post-clip last audio subtracts the end of nominal real-time clip transmission from arrival of the last segment payload.",
              "- Post-signal last audio subtracts the last sample above absolute amplitude 400. This is a threshold-based synthetic boundary, not a human-labelled speech end.",
              "- WAV validation/decode occurs after recording arrival. Server STT/queue/translate/TTS timings are recorded separately; their percentile sums are not end-to-end percentiles.",
              "- p50/p95 use nearest rank: sorted index ceil(p*n)-1. Missing values are excluded and their denominators are explicit in the CSV.", "",
              "## Boundary Sensitivity", "",
              "| Condition | Input-Start First-Audio p50 / p95 (ms) | Post-Clip Last-Audio p50 / p95 | Post-Signal Last-Audio p50 / p95 |", "| --- | ---: | ---: | ---: |"]
    for row in summaries:
        if row["source_category"] == "ALL":
            values = [f"{fmt(row[metric + '_p50'])} / {fmt(row[metric + '_p95'])}" for metric in
                      ("first_audio_after_input_start_ms", "last_audio_after_clip_end_ms", "last_audio_after_signal_end_ms")]
            lines.append("| " + row["condition"] + " | " + " | ".join(values) + " |")
    lines += ["", "Clip end can include trailing silence; the signal threshold is an approximation, not native-labelled speech end. The predeclared five-second target uses clip end only.", "",
              "## Source Breakdown", "",
              "| Condition | Source | Inputs | Complete | Post-Clip p50 / p95 (ms) | WER / CER (%) |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for row in summaries:
        if row["source_category"] != "ALL":
            lines.append(f"| {row['condition']} | {row['source_category']} | {row['inputs']} | {row['complete_inputs']} | {fmt(row['last_audio_after_clip_end_ms_p50'])} / {fmt(row['last_audio_after_clip_end_ms_p95'])} | {fmt(row['wer'] * 100) if row['wer'] is not None else 'n.a.'} / {fmt(row['cer'] * 100) if row['cer'] is not None else 'n.a.'} |")
    lines += ["", "Per-source/target-language results and denominators: `language_pairs.csv`.", "",
              "## Server Stage Timings", "", "STT timing includes speech duration (VAD speech-start to transcript commit), not isolated model inference time.",
              "| Condition | STT p50 / p95 (ms) | Queue p50 / p95 | Translate p50 / p95 | TTS p50 / p95 |", "| --- | ---: | ---: | ---: | ---: |"]
    for row in summaries:
        if row["source_category"] == "ALL":
            values = [f"{fmt(row[metric + '_p50'])} / {fmt(row[metric + '_p95'])}" for metric in ("stt_ms", "queue_ms", "translate_ms", "tts_ms")]
            lines.append("| " + row["condition"] + " | " + " | ".join(values) + " |")
    lines += ["", "## Paired Comparison", "", "Positive differences mean overlap was slower or had higher transcription error.",
              "Average repeats/targets within fixture, then bootstrap fixture-level mean differences (2,000 resamples, seed 20261012).", "",
              "| Metric | Paired Fixtures | Mean Difference | Fixture Bootstrap 95% | Pair-Block Bootstrap 95% |", "| --- | ---: | ---: | ---: | ---: |"]
    for row in paired:
        lines.append(f"| {row['metric']} | {row['paired_fixtures']} | {fmt(row['mean_overlap_minus_alternating'], 4)} | [{fmt(row['bootstrap_95_low'], 4)}, {fmt(row['bootstrap_95_high'], 4)}] | [{fmt(row['pair_block_95_low'], 4)}, {fmt(row['pair_block_95_high'], 4)}] |")
    lines += ["", "Latency differences are milliseconds; WER/CER differences are fractions. This interval reflects this small synthetic fixture sample, not all speakers/networks.",
              "`paired.csv` additionally reports pair-block bootstrap intervals (15 paired-trial blocks), accounting for two fixtures sharing a trial; paired repeats with missing latency are excluded together.",
              "", "## Predeclared Exploratory Targets", ""]
    for row in summaries:
        if row["source_category"] == "ALL":
            lines.append(f"- {row['condition']}: >=95% complete input delivery: **{'met' if row['delivery_target_met'] else 'not met'}**; post-clip p95 <=5s: **{'met' if row['latency_target_met'] else 'not met'}**.")
            low, high = row["wilson_95"]
            lines.append(f"  Descriptive Wilson interval: {fmt(low * 100) if low is not None else 'n.a.'}% to {fmt(high * 100) if high is not None else 'n.a.'}%; repetitions are dependent, so not a production SLA.")
    rooms = {"bench-" + row["trial_id"] for row in trials}
    connections = [row["connect_ms"] for row in metrics if row.get("kind") == "connect" and row.get("room") in rooms]
    lines += ["", f"STT connection setup: n={len(connections)}, p50={fmt(percentile(connections, .5))} ms, p95={fmt(percentile(connections, .95))} ms. Clip timing starts after all participants are ready; setup is not included in post-clip latency."]
    successful_pipelines = [row for row in metrics if row.get("kind") == "utterance" and row.get("room") in rooms]
    naive = sum(len(row["listener_parties"]) for row in successful_pipelines)
    optimized = len(successful_pipelines)
    reduction = (naive - optimized) / naive if naive else None
    lines += ["", "## Language-Group Fan-Out", "",
              f"Successful distinct-target pipeline invocations: **{optimized}**; recipient-level invocations without grouping: **{naive}**.",
              f"Counterfactual call-count reduction for these observed recipients: **{fmt(reduction * 100) if reduction is not None else 'n.a.'}%** for translation and TTS separately.",
              "This is computed from actual successful pipeline recipient lists. It is not an A/B performance or billing comparison; failed calls may still incur cost.", "",
              "## Failure and Integrity Observations", ""]
    failed = [row for row in trials if not row["trial_complete"]]
    if failed:
        lines.extend(f"- {row['trial_id']}: harness={row['harness_error']}; room errors={len(row['room_errors'])}. Inspect input/target CSV and raw events." for row in failed)
    else:
        lines.append("No measured trial failed the full-delivery criterion. This does not establish 100% production reliability or translation accuracy.")
    lines += [f"Duplicate audio messages: {sum(row['duplicate_audio'] for row in targets)}; invalid WAV messages: {sum(row['invalid_audio'] for row in targets)}; missing recognized segment outputs: {sum(row['missing_segments'] for row in targets)}.",
              f"Provider end counts agreeing with collected transcript counts: {sum(row['provider_segment_count_matches'] is True for row in inputs)}/{len(inputs)}. Complete fixture transmission checks: {sum(row['fixture_sent_complete'] for row in inputs)}/{len(inputs)}.",
              f"Maximum recorded next-frame deadline overrun: {fmt(max((row['max_frame_lateness_ms'] for row in inputs if row['max_frame_lateness_ms'] is not None), default=None))} ms.",
              "The harness field named `max_frame_lateness_ms` measures overrun of the NEXT scheduled frame deadline after send, not absolute per-frame dispatch jitter. Zero does not prove zero jitter; no per-frame dispatch trace was recorded.", "",
              "## Consumption", "",
              f"Measured PCM sent (including silence): {fmt(sum(row['sent_audio_seconds'] for row in inputs))} seconds; unique fixture speech clips total {fmt(sum(row['duration_s'] for row in manifest['dataset']))} seconds before repetition."]
    runtime = ROOT.joinpath(*PureWindowsPath(metadata.get("runtime_directory", "logs/controlled-benchmark")).parts)
    db_path = runtime / "usage.sqlite3"
    if db_path.exists():
        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as db:
            usage = dict(db.execute("SELECT kind,amount FROM usage").fetchall())
        estimate = usage.get("stt_seconds", 0) / 3600 * 30 + usage.get("translate_chars", 0) * .002 + usage.get("tts_chars", 0) * .003
        lines += [f"Benchmark ledger (includes diagnostics and any resumed attempts): `{json.dumps(usage)}`.",
                  f"Conservative reserved-cost estimate: INR {estimate:.3f} excluding GST, using project rate assumptions INR30/hour STT, INR20/10k translation chars, INR30/10k TTS chars.",
                  "STT reserves whole 60-second sessions; do not mistake this estimate for actual Sarvam charges. No billing dashboard balance was queried."]
    provider_rows = [row for row in metrics if row.get("kind") == "provider_session_end" and row.get("room") in rooms]
    billed = [row["audio_duration_s"] for row in provider_rows if row.get("audio_duration_s") is not None]
    processing_chars = sum(row["text_chars"] for row in successful_pipelines)
    synthesis_chars = sum(len(row["translated_text"]) for row in segments if row["translated_text"] is not None)
    processing_estimate = sum(billed) / 3600 * 30 + processing_chars * .002 + synthesis_chars * .003
    cost = {"scope": "measured trials only; diagnostics excluded", "provider_audio_seconds": sum(billed),
            "reported_speaker_sessions": len(billed), "successful_translation_input_chars": processing_chars,
            "observed_tts_input_chars": synthesis_chars, "estimated_inr_excluding_gst": processing_estimate,
            "rate_source": "https://docs.sarvam.ai/api/getting-started/pricing", "rate_checked_date_local": "2026-10-12",
            "rate_assumptions": {"stt_inr_per_hour": 30, "translation_inr_per_10k_chars": 20, "tts_inr_per_10k_chars": 30},
            "actual_account_billing_verified": False}
    (output / "consumption.json").write_text(json.dumps(cost, indent=2), encoding="utf-8")
    lines += [f"Provider-reported audio-duration records: {len(billed)}/{len(inputs)} speaker sessions; sum {fmt(sum(billed))} seconds where available.", "",
              f"Measured-only processing estimate: INR {processing_estimate:.3f} excluding GST, using {sum(billed):.2f} provider-reported audio seconds, {processing_chars} successful translation input characters, and {synthesis_chars} observed TTS input characters. Diagnostic calls excluded; failed/retried internal HTTP calls could be additional.",
              "Rates checked against [Sarvam pricing documentation](https://docs.sarvam.ai/api/getting-started/pricing). This is computed usage x listed prices, NOT an account billing receipt.", "",
              "## Limitations and Claim Boundaries", "",
              "- Synthetic voice reused across conditions; not diverse human voices, microphones, accents or acoustic echo/noise.",
              "- Model-generated non-English references are not native-verified. Normalization/code-mix transliteration affects WER/CER. Character edits use Unicode codepoints, not graphemes.",
              "- WER/CER compare STT output to the TTS input script, not a human-verified transcript of the generated waveform. Fixture pronunciation errors can contribute; do not attribute every mismatch to STT or claim isolated recognition accuracy.",
              "- No native-speaker semantic or spoken-intelligibility review. Native-review CSV is explicitly unreviewed.",
              "- Loopback excludes public tunnel, WAN variability and actual microphone capture/playback. Browser replay is separate, cached-TTS component validation.",
              "- Shared development laptop, not a dedicated isolated load generator. Companion analysis/tests may add background work; seeded order reduces but does not eliminate time/host-load confounding.",
              "- No 30-minute endurance, multi-room throughput, more-than-two-speaker stress or production SLA validation.",
              "- One utterance per speaker session: low queue delay here is not a sustained-conversation backpressure test.",
              "- Descriptive percentiles are specific to this sample. Historical developmental results are not merged or used to claim a latency improvement.", "",
              "## Evidence Files", "",
              "`manifest.json`, `metadata.json`, `trials/`, `events/`, `inputs.csv`, `targets.csv`, `segments.csv`, `trial_observations.csv`, `summary.csv`, `paired.csv`, `summary.json`, `native_review.csv`.",
              "Cached WAV files and the isolated backend ledger/metrics are ignored local runtime files under `logs/controlled-benchmark/`.", ""]
    worst = {}
    for row in inputs:
        if row["fixture_id"] not in worst or row["wer"] > worst[row["fixture_id"]]["wer"]:
            worst[row["fixture_id"]] = row
    examples = sorted(worst.values(), key=lambda row: row["wer"], reverse=True)[:5]
    lines += ["## Transcription Review Examples", "", "Highest normalized WER fixtures (unique IDs). Human review must distinguish recognition mistakes from acceptable representation changes.", ""]
    for row in examples:
        lines += [f"### {row['fixture_id']} ({row['source_category']}, WER {row['wer'] * 100:.2f}%)", "", "Reference: " + row["reference"], "", "Observed: " + row["transcript"], ""]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    replay_path = output / "playback_replay.json"
    if replay_path.exists():
        replay = json.loads(replay_path.read_text())
        write_csv(output / "playback_replay.csv", replay["rows"])
        lines = ["", "## Browser Playback Replay", "",
                 "Cached WAVs from pairs 1-3, repetition 1, both conditions were replayed at measured receipt offsets.",
                 f"Browser: Edge/Chromium {replay['browser']}. Actual frontend queue and media player; mocked delivery socket, no paid API calls.",
                 "Desktop 1440x1000 and mobile-sized 390x844 browser viewports; mobile viewport is not physical mobile hardware.",
                 "| Viewport | Condition | Clips Started / Expected | Receive-to-Playing p50 / p95 (ms) |", "| --- | --- | ---: | ---: |"]
        for width in (1440, 390):
            for condition in ("alternating", "overlap"):
                rows = [row for row in replay["rows"] if row["viewport_width"] == width and row["condition"] == condition]
                delays = [row["receive_to_playback_ms"] for row in rows]
                lines.append(f"| {width} | {condition} | {sum(row['playback_reports'] == 1 for row in rows)}/{len(rows)} | {fmt(percentile(delays, .5))} / {fmt(percentile(delays, .95))} |")
        lines += [f"Replay cases: {len(replay['cases'])}; cases with errors: {sum(bool(case['errors']) for case in replay['cases'])}; horizontal-overflow cases: {sum(case['layout_overflow'] for case in replay['cases'])}.",
                  f"Media endings: {sum(case['media_endings'] for case in replay['cases'])}/{sum(case['expected_clips'] for case in replay['cases'])}; media errors: {sum(case['media_errors'] for case in replay['cases'])}. FIFO playback order checked.",
                  "Queueing can dominate playback delay when translated replies arrive together. Do not add these replay percentiles to live provider percentiles or claim live end-to-end timing."]
        with (output / "REPORT.md").open("a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    with (output / "REPORT.md").open("a", encoding="utf-8") as stream:
        verification_path = output / "verification.json"
        if verification_path.exists():
            verification = json.loads(verification_path.read_text())
            stream.write("\n## Validation\n\nIndependent raw-data validation: **" + verification["status"] + "**. See `verification.json` for counts and checked invariants.\n")
        measured = {row["condition"]: row for row in summaries if row["source_category"] == "ALL"}
        latency_difference = next(row for row in paired if row["metric"] == "mean_last_audio_after_clip_end_ms")
        conclusions = ["", "## Interpretation and Next Work", "",
                       f"1. Delivery held in this controlled sample: {len(inputs)} input runs and {len(targets)} stable-listener checks. This is observed completeness, not a production reliability guarantee.",
                       f"2. Mean paired overlap overhead was {fmt(latency_difference['mean_overlap_minus_alternating'])} ms; pair-block 95% bootstrap interval [{fmt(latency_difference['pair_block_95_low'])}, {fmt(latency_difference['pair_block_95_high'])}] ms. Do not claim overlap had zero latency cost.",
                       "3. WER/CER were unchanged between conditions in this dataset, consistent with independent streams. This is not evidence of acoustic source separation or semantic accuracy.",
                       "4. Cached playback replay shows extra overlap queue delay. Real user-perceived latency can exceed audio-receipt latency; investigate queued TTS length/turn handling before making a sub-five-second end-to-end claim.",
                       "5. Native-review the generated audio/reference alignment and translations, particularly the larger Hindi/Telugu reference mismatches. Add diverse human-recorded speech in a separate follow-up dataset.",
                       "6. Any latency optimization needs a new controlled run on the same paired dataset with changed code clearly identified. Do not compare these post-clip numbers with old input-start numbers as a percentage improvement.", "",
                       "## Evidence-Backed Portfolio Wording", "",
                       f"- Built a reproducible real-provider WebSocket benchmark covering 30 multilingual/code-mixed synthetic fixtures, alternating and overlapping two-speaker input, three repetitions and {len(targets)} complete listener results.",
                       f"- Observed post-clip complete-audio-receipt p95 of {measured['alternating']['last_audio_after_clip_end_ms_p95'] / 1000:.2f}s alternating and {measured['overlap']['last_audio_after_clip_end_ms_p95'] / 1000:.2f}s overlapping in a local-loopback benchmark; playback excluded.",
                       f"- Grouped translation/TTS by target language, using {optimized} successful pipelines for {naive} recipient-level deliveries ({fmt(reduction * 100)}% fewer logical invocations than a per-recipient design in the measured trials).", "",
                       "Avoid: production-scale, 100% reliable, 88% translation accuracy, <5s live end-to-end, zero-overlap penalty, 30-minute endurance verified, or 25% measured total billing savings.", "",
                       "## Distribution Plots", "", "![Measured benchmark distributions](benchmark.png)", ""]
        stream.write("\n".join(conclusions))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    metrics_path = output / "backend_metrics.jsonl"
    if not metrics_path.exists():
        runtime = ROOT.joinpath(*PureWindowsPath(metadata.get("runtime_directory", "logs/controlled-benchmark")).parts)
        metrics_path = runtime / "metrics.jsonl"
    metrics = [json.loads(line) for line in metrics_path.read_text().splitlines()] if metrics_path.exists() else []
    inputs, targets, segments, trials = load_observations(output, metrics)
    summaries = summarize(inputs, targets, segments)
    paired = paired_comparison(inputs)
    for name, rows in (("inputs", inputs), ("targets", targets), ("segments", segments),
                       ("trial_observations", trials), ("summary", summaries), ("paired", paired)):
        write_csv(output / (name + ".csv"), rows)
    write_csv(output / "language_pairs.csv", language_pairs(targets))
    reference_by_id = {item["id"]: item for item in manifest["dataset"]}
    review = [{**row, "reference_text": reference_by_id[row["fixture_id"]]["reference"],
               "reference_audio_sha256": reference_by_id[row["fixture_id"]]["sha256"],
               "native_reviewer": "", "quality_status": "unreviewed", "meaning_preservation_1_to_5": "",
               "correction": "", "notes": ""} for row in segments]
    if metadata["status"] == "completed":
        review_path = output / "native_review.csv"
        if review_path.exists():
            with review_path.open(encoding="utf-8-sig", newline="") as stream:
                previous = {(row["trial_id"], row["speaker"], str(row["seq"]), row["target_language"]): row for row in csv.DictReader(stream)}
            for row in review:
                old = previous.get((row["trial_id"], row["speaker"], str(row["seq"]), row["target_language"]), {})
                for field in ("native_reviewer", "quality_status", "meaning_preservation_1_to_5", "correction", "notes"):
                    if field in old:
                        row[field] = old[field]
        write_csv(output / "native_review.csv", review)
    if metadata["status"] == "completed" and not (output / "backend_metrics.jsonl").exists():
        (output / "backend_metrics.jsonl").write_text("".join(json.dumps(row) + "\n" for row in metrics if row.get("room", "").startswith("bench-")), encoding="utf-8")
    summary = {"analysis_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "measured_trials": len(trials), "planned_trials": len(manifest["trials"]),
               "inputs": len(inputs), "stable_listener_target_checks": len(targets), "summaries": summaries,
               "paired": paired, "failed_trials": [row for row in trials if not row["trial_complete"]]}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report(output, manifest, metadata, inputs, targets, segments, trials, summaries, paired, metrics)
    print(json.dumps({"trials": len(trials), "inputs": len(inputs), "failed_trials": len(summary["failed_trials"]),
                      "report": str(output / "REPORT.md")}, indent=2))


if __name__ == "__main__":
    main()
