"""Generate a readable benchmark report and an explicitly unreviewed quality sheet."""
import argparse
import csv
import json
from pathlib import Path

from run_structured_test import LISTENER_LANGUAGES, write_summary


def report(raw_path):
    entries = json.loads(raw_path.read_text(encoding="utf-8"))
    summary_path = raw_path.with_name(raw_path.stem + "_summary.csv")
    write_summary(entries, summary_path)
    review_path = raw_path.with_name(raw_path.stem + "_review.csv")
    # Never overwrite human review work on a later reporting pass.
    if not review_path.exists():
        with review_path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["category", "index", "expected_text", "transcript", "target_language",
                             "translation", "native_reviewer", "quality_status", "correction", "notes"])
            for entry in entries:
                for language in LISTENER_LANGUAGES:
                    data = entry.get("results", {}).get(language) or {}
                    writer.writerow([entry["category"], entry["index"], entry["text"], entry.get("transcript"),
                                     language, data.get("translated_text"), "", "unreviewed", "", ""])
    with summary_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    finished = sum(bool(e.get("complete")) for e in entries)
    lines = ["# Structured benchmark", "", f"Completed utterances: {finished}/{len(entries)} recorded; full fixture set: 92.",
             "", "Synthetic PCM input, one speaker and four listen-only targets, real-time paced audio.",
             "End-to-end here means speaker send-start to the first translated audio receipt; it excludes playback.",
             "VAD can split a clip into multiple transcripts; first-audio timing does not prove full-sentence delivery.",
             "Each category labels the scripted input, not the language detected by STT.",
             "Native-speaker quality review has not been performed.", "",
             "| Source | Target | Samples | Missing | Receive p50 (ms) | Receive p95 (ms) |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['source']} | {row['target']} | {row['n']} | {row['n_missing']} | {row['end_to_end_ms_p50']} | {row['end_to_end_ms_p95']} |")
    lines.extend(["", "Small-sample percentiles are descriptive only. Stage timings overlap with input duration;",
                  "do not describe this as speech-end-to-playback latency or human translation accuracy.", "",
                  f"Raw data: `{raw_path.name}`. Native review worksheet: `{review_path.name}`.", ""])
    metadata_path = raw_path.with_name(raw_path.stem + "_metadata.json")
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        lines.extend(["## Campaign context", "",
                      f"Controlled retries: {len(metadata.get('controlled_retries', []))}. Zero final missing results does not imply zero failed attempts.",
                      "The first 14 successful samples preceded an explicit 0.8-second VAD silence tail; later samples included it.",
                      "These are developmental measurements, not a controlled production SLA study.", "",
                      f"Conservative usage estimate: INR {metadata['usage_reservations']['estimated_inr_excluding_gst']} excluding GST.",
                      "Reservations cover entire bounded STT sessions, even when a clip ends earlier. Actual Sarvam billing was not queried.", ""])
    output = raw_path.with_name(raw_path.stem + "_report.md")
    output.write_text("\n".join(lines), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    report(parser.parse_args().raw)
