"""Independent integrity/count/percentile checks for the finished campaign."""
import csv
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "loadtest/results/controlled_20261012"


def check():
    manifest = json.loads((OUTPUT / "manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((OUTPUT / "metadata.json").read_text())
    assert metadata["status"] == "completed", "Campaign still incomplete"
    assert hashlib.sha256((ROOT / "loadtest/controlled_benchmark.py").read_bytes()).hexdigest() == metadata["harness_sha256"]
    assert hashlib.sha256((ROOT / "backend/app.py").read_bytes()).hexdigest() == metadata["backend_sha256"]
    assert len(manifest["trials"]) == 90 and len(manifest["dataset"]) == 30
    checks = 0
    provider_metrics = [json.loads(line) for line in (OUTPUT / "backend_metrics.jsonl").read_text().splitlines()]
    for spec in manifest["trials"]:
        trial = json.loads((OUTPUT / "trials" / (spec["id"] + ".json")).read_text(encoding="utf-8"))
        assert trial["spec"] == spec
        raw = OUTPUT.joinpath(*PureWindowsPath(trial["events_file"]).parts)
        assert raw.resolve().is_relative_to(OUTPUT.resolve())
        assert hashlib.sha256(raw.read_bytes()).hexdigest() == trial["events_sha256"]
        events = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
        assert not any(key in event for event in events for key in ("access_token", "resume_token", "data", "api_subscription_key"))
        for slot in (3, 4):
            party = trial["parties"][str(slot)]
            fixture = spec["inputs"][slot - 3]
            pcm = ROOT / "loadtest/test_fixtures" / (fixture["id"] + ".pcm")
            assert hashlib.sha256(pcm.read_bytes()).hexdigest() == fixture["sha256"]
            final_sequences = {event["seq"] for event in events if event["receiver"] == 0 and event["type"] == "transcript"
                               and event.get("kind") == "final" and event.get("party") == party}
            assert final_sequences
            end = [row for row in provider_metrics if row.get("kind") == "provider_session_end"
                   and row.get("room") == trial["room"] and row.get("party") == party]
            assert len(end) == 1 and end[0]["total_utterances"] == len(final_sequences)
            for receiver, target in enumerate(spec["targets"]):
                audio = [event for event in events if event["receiver"] == receiver and event["type"] == "audio" and event["speaker"] == party]
                assert {event["seq"] for event in audio} == final_sequences
                assert len(audio) == len(final_sequences)
                assert all(event["language"] == target and event["wav_valid"] for event in audio)
                for event in audio:
                    wav = ROOT / "logs/controlled-benchmark/audio" / spec["id"] / f"l{receiver}-{party}-{event['seq']}.wav"
                    assert hashlib.sha256(wav.read_bytes()).hexdigest() == event["wav_sha256"]
                checks += 1
    with (OUTPUT / "targets.csv").open(encoding="utf-8-sig", newline="") as stream:
        targets = list(csv.DictReader(stream))
    assert checks == len(targets) == 540
    summary = json.loads((OUTPUT / "summary.json").read_text())
    assert summary["inputs"] == 180 and not summary["failed_trials"]
    for condition in ("alternating", "overlap"):
        rows = [row for row in targets if row["condition"] == condition]
        values = sorted(float(row["last_audio_after_clip_end_ms"]) for row in rows)
        recorded = next(row for row in summary["summaries"] if row["condition"] == condition and row["source_category"] == "ALL")
        assert len(values) == 270
        for quantile, suffix in ((.5, "p50"), (.95, "p95")):
            independently_computed = values[math.ceil(quantile * len(values)) - 1]
            assert math.isclose(independently_computed, recorded["last_audio_after_clip_end_ms_" + suffix], abs_tol=1e-8)
    result = {"status": "passed", "measured_trials": 90, "input_runs": 180, "target_checks": checks,
              "checks": ["fixed manifests", "build hashes", "fixture/event/WAV hashes", "secret field exclusion",
                         "provider-end segment counts", "complete stable-listener sequence sets", "independent nearest-rank percentiles"]}
    (OUTPUT / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    check()
