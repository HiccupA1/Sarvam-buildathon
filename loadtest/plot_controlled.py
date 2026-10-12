"""Render measured distributions; uses bundled matplotlib, no API calls."""
import argparse
import csv
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / "logs/controlled-benchmark/matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    output = parser.parse_args().output
    with (output / "targets.csv").open(encoding="utf-8-sig", newline="") as stream:
        targets = list(csv.DictReader(stream))
    with (output / "inputs.csv").open(encoding="utf-8-sig", newline="") as stream:
        inputs = list(csv.DictReader(stream))
    summary = json.loads((output / "summary.json").read_text())
    colors = {"alternating": "#167d8d", "overlap": "#d05b35"}
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "figure.facecolor": "white"})
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    for condition, color in colors.items():
        values = sorted(float(row["last_audio_after_clip_end_ms"]) / 1000 for row in targets
                        if row["condition"] == condition and row["last_audio_after_clip_end_ms"])
        axes[0, 0].plot(values, [(i + 1) / len(values) for i in range(len(values))], color=color, label=f"{condition} (n={len(values)})")
    axes[0, 0].axvline(5, color="#777777", linestyle="--", linewidth=1, label="predeclared 5s target")
    axes[0, 0].set(title="Complete Audio Receipt Distribution", xlabel="Seconds after input clip end", ylabel="Cumulative fraction")
    axes[0, 0].legend(fontsize=8, loc="lower right")
    fixture_means = {}
    for row in inputs:
        if row["mean_last_audio_after_clip_end_ms"]:
            fixture_means.setdefault(row["fixture_id"], {}).setdefault(row["condition"], []).append(float(row["mean_last_audio_after_clip_end_ms"]))
    differences = sorted(sum(group["overlap"]) / len(group["overlap"]) - sum(group["alternating"]) / len(group["alternating"])
                         for group in fixture_means.values() if "overlap" in group and "alternating" in group)
    axes[0, 1].scatter(range(1, len(differences) + 1), differences, color="#34415a", s=28)
    axes[0, 1].axhline(0, color="#888888", linewidth=1)
    axes[0, 1].set(title="Paired Fixture Differences", xlabel="Fixture rank (sorted difference)", ylabel="Overlap minus alternating (ms)")
    categories = ["en", "hi", "ta", "te", "mix"]
    for j, (condition, color) in enumerate(colors.items()):
        rows = {row["source_category"]: row for row in summary["summaries"] if row["condition"] == condition}
        axes[1, 0].bar([i + (j - .5) * .35 for i in range(5)], [rows[category]["wer"] * 100 for category in categories],
                       width=.35, color=color, label=condition)
    axes[1, 0].set(title="Normalized Transcription Error", ylabel="Reference word error rate (%)", xticks=range(5), xticklabels=categories)
    axes[1, 0].legend(fontsize=8)
    rows = [row for row in summary["summaries"] if row["source_category"] == "ALL"]
    axes[1, 1].bar([row["condition"] for row in rows], [row["complete_inputs"] for row in rows], color=list(colors.values()))
    for i, row in enumerate(rows):
        axes[1, 1].text(i, row["complete_inputs"] + 1, f"{row['complete_inputs']}/{row['inputs']}", ha="center")
    axes[1, 1].set(title="Recognized-Segment Delivery", ylabel="Complete input runs", ylim=(0, max(row["inputs"] for row in rows) * 1.15))
    fig.suptitle("Controlled Two-Speaker Benchmark | Synthetic Speech, Real Sarvam", fontsize=16, fontweight="bold")
    fig.savefig(output / "benchmark.png", dpi=160)
    plt.close(fig)
    print(output / "benchmark.png")


if __name__ == "__main__":
    main()
