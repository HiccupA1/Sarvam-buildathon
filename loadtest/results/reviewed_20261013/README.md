# Bilingual human review evidence

This directory supplements the unchanged 12 October 2026 controlled benchmark
with human text/audio review supplied on 13 October 2026. The project owner
confirmed that bilingual humans reviewed the text and listened to the audio.

| File | Purpose |
| --- | --- |
| `human_review_submission.csv` | Byte-preserved submission: 270 rows, 90 scored and 180 unreviewed |
| `reviewed_observations.csv` | The 90 scored observations with subset and threshold fields |
| `human_review_summary.json` | Counts, score distributions, subgroup results, source hashes, and import audit |

All 90 scores cover 30 unique fixtures in alternating condition, repetition 1.
77/90 outputs scored at least 4/5 for meaning preservation. Excluding same-language
outputs, 62/72 cross-language outputs met that threshold (86.1%). The remaining
outputs and their original reviewer notes are retained, including meaning errors.

The samples are synthetic source clips. Reviewer labels do not independently
establish identities or credentials. There was no blind second-rating protocol,
inter-rater agreement calculation, or separate audio-intelligibility score.
A 4/5 threshold is a descriptive quality criterion, not customer satisfaction,
task completion, or universal translation accuracy. Overlap outputs were not
human-reviewed in this submission.

Read the [consolidated report](../../../docs/TESTING-REPORT.md) for methodology,
limitations, timing measurements, and the error catalogue.

## Reproduce the review analysis offline

From the repository root, using Python 3.12 and only its standard library:

```powershell
.venv\Scripts\python.exe scripts\build_testing_report.py --review loadtest/results/reviewed_20261013/human_review_submission.csv --skip-pdf
```

The command validates observation keys, immutable measurement fields, allowed
spreadsheet rounding, score completeness, and raw latency percentiles. It rebuilds
the Markdown report and derived CSV/JSON. It makes no live Sarvam calls.

Optional PDF export also needs `reportlab` and `Pillow`; omit `--skip-pdf` after
installing those packages in your Python environment. Source WAVs are not embedded
in this repository; the documented cached playback rerun needs the local WAVs.

Submission SHA-256:
`a8d754c7dec377a1c19d165cba4c77d0a7925ff41e32aa3648f1ad209cb73837`.
