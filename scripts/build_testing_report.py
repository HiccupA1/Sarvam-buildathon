"""Compile cached benchmark evidence and an immutable human-review submission."""

import argparse
import csv
import hashlib
import json
import math
import shutil
import statistics
from collections import Counter, defaultdict
from decimal import Decimal
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "loadtest/results/controlled_20261012"
OUT = ROOT / "loadtest/results/reviewed_20261013"
PDF = ROOT / "output/pdf/Sarvam-Testing-Report.pdf"
MD = ROOT / "docs/TESTING-REPORT.md"
LABELS = {"en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "mix": "Tamil-English mix"}
KEYS = ("trial_id", "fixture_id", "speaker", "target_language", "receiver", "seq")
MANUAL = {"native_reviewer", "quality_status", "meaning_preservation_1_to_5", "correction", "notes"}
REPORT_DATE = "2026-10-13"


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key(row):
    return tuple(row[field] for field in KEYS)


def quality(rows):
    scores = [int(row["meaning_preservation_1_to_5"]) for row in rows]
    n = len(scores)
    return {
        "n": n, "score_sum": sum(scores), "mean": statistics.mean(scores) if n else None,
        "median": statistics.median(scores) if n else None,
        "score_4_or_5": sum(score >= 4 for score in scores),
        "score_4_or_5_pct": 100 * sum(score >= 4 for score in scores) / n if n else None,
        "below_4": sum(score < 4 for score in scores),
        "distribution": {str(score): scores.count(score) for score in range(1, 6)},
        "fixtures": len({row["fixture_id"] for row in rows}),
    }


def group_quality(rows, fields):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[field] for field in fields)].append(row)
    return [{**dict(zip(fields, group)), **quality(values)} for group, values in sorted(groups.items())]


def analyze(source):
    submitted = read_csv(source)
    baseline = read_csv(BENCH / "native_review.csv")
    reference = {key(row): row for row in baseline}
    assert len(reference) == len(baseline) == 540
    assert len({key(row) for row in submitted}) == len(submitted), "Duplicate submitted observation keys"
    assert submitted and set(submitted[0]) == set(baseline[0]), "Unexpected review columns"
    rounding = Counter()
    maxima = defaultdict(Decimal)
    for row in submitted:
        assert key(row) in reference, f"Unmatched review row: {key(row)}"
        original = reference[key(row)]
        for field in row:
            if field in MANUAL or row[field] == original[field]:
                continue
            if field == "valid_delivered" and row[field].lower() == original[field].lower():
                continue
            # Spreadsheet exports round display precision; never substitute these for raw timings.
            tolerances = {"audio_received_ms": Decimal("0.005"), "wav_duration_s": Decimal("0.0000005")}
            if field in tolerances:
                difference = abs(Decimal(row[field]) - Decimal(original[field]))
                assert difference <= tolerances[field], f"Changed measurement: {key(row)}, {field}"
                rounding[field] += 1
                maxima[field] = max(maxima[field], difference)
            else:
                raise AssertionError(f"Changed non-review field: {key(row)}, {field}")
    reviewed = []
    for row in submitted:
        score = row["meaning_preservation_1_to_5"].strip()
        if not score:
            assert row["quality_status"] == "unreviewed", "Missing score marked reviewed"
            continue
        assert row["quality_status"] == "reviewed" and row["native_reviewer"]
        assert score in {"1", "2", "3", "4", "5"}, "Invalid rating"
        reviewed.append(row)
    same = [row for row in reviewed if row["source_category"] == row["target_language"][:2]]
    cross = [row for row in reviewed if row not in same]
    inputs = read_csv(BENCH / "inputs.csv")
    targets = read_csv(BENCH / "targets.csv")
    summary = json.loads((BENCH / "summary.json").read_text())
    # Independently reconcile the stored headline timing statistics from raw target observations.
    for condition in ("alternating", "overlap"):
        values = sorted(float(row["last_audio_after_clip_end_ms"]) for row in targets if row["condition"] == condition)
        reported = next(row for row in summary["summaries"] if row["condition"] == condition and row["source_category"] == "ALL")
        assert len(values) == reported["target_checks"] == 270
        for p, suffix in ((0.5, "p50"), (0.95, "p95")):
            assert math.isclose(values[math.ceil(p * len(values)) - 1], reported["last_audio_after_clip_end_ms_" + suffix], abs_tol=1e-8)
    assert len(inputs) == summary["inputs"] == 180
    assert len(targets) == summary["stable_listener_target_checks"] == 540
    fixture_groups = group_quality(reviewed, ["fixture_id"])
    fixture_all_acceptable = sum(group["below_4"] == 0 for group in fixture_groups)
    historical = []
    for path in sorted((ROOT / "loadtest/results").glob("load_test_*.json")):
        sessions = json.loads(path.read_text())
        event_counts = Counter(event.get("type") for session in sessions for events in session.get("events", {}).values() for event in events)
        historical.append({"file": path.name, "sessions": len(sessions), "events": dict(event_counts)})
    result = {
        "report_date": REPORT_DATE,
        "review_source_sha256": digest(source),
        "submission_rows": len(submitted), "matched_rows": len(submitted),
        "reviewed_rows": len(reviewed), "unreviewed_submission_rows": len(submitted) - len(reviewed),
        "unsubmitted_benchmark_rows": len(baseline) - len(submitted),
        "review_conditions": dict(Counter(row["condition"] for row in reviewed)),
        "review_repetitions": dict(Counter(row["repetition"] for row in reviewed)),
        "reviewer_labels": dict(Counter(row["native_reviewer"] for row in reviewed)),
        "review_method_confirmation": {
            "confirmed_by": "project owner in chat", "date": REPORT_DATE,
            "bilingual_human_reviewers": True, "audio_listened_to": True,
            "reviewer_identities_credentials_and_listening_logs": "not supplied",
            "separate_audio_intelligibility_scores": "not supplied",
        },
        "all_reviewed": quality(reviewed), "cross_language": quality(cross), "same_language": quality(same),
        "by_source": group_quality(reviewed, ["source_category"]),
        "by_target": group_quality(reviewed, ["target_language"]),
        "by_source_target": group_quality(reviewed, ["source_category", "target_language"]),
        "fixtures_all_targets_score_4_or_5": fixture_all_acceptable,
        "fixtures_with_score_below_4": len(fixture_groups) - fixture_all_acceptable,
        "fixtures_with_any_score_below_5": sum(group["distribution"]["5"] != group["n"] for group in fixture_groups),
        "rounding_changes": dict(rounding), "maximum_rounding_difference": {k: str(v) for k, v in maxima.items()},
        "historical_smoke_runs": historical,
        "source_hashes": {p.name: digest(p) for p in [BENCH / "summary.json", BENCH / "targets.csv", BENCH / "inputs.csv", BENCH / "playback_replay.json", BENCH / "consumption.json"]},
    }
    return result, submitted, reviewed, summary


def table(headers, rows):
    return {"kind": "table", "headers": headers, "rows": rows}


def paragraph(text):
    return {"kind": "paragraph", "text": text}


def make_sections(data, reviewed, summary):
    q = data["all_reviewed"]
    cross = data["cross_language"]
    same = data["same_language"]
    all_summaries = [row for row in summary["summaries"] if row["source_category"] == "ALL"]
    parts = []

    def section(title, *blocks):
        parts.append({"title": title, "blocks": list(blocks)})

    section("Executive Summary",
        paragraph("Sarvam Interpreter is a browser-based speech-to-speech translation application for English, Hindi, Tamil and Telugu, including Tamil-English code-mixed inputs. This report combines the completed real-provider controlled benchmark, user-supplied bilingual human reviews, cached browser playback validation and earlier developmental tests. It assesses an application integrating Sarvam models, not a newly trained speech or translation model."),
        table(["Evidence", "Observed result", "Meaning"], [
            ["Controlled delivery", "180/180 inputs; 540/540 stable-listener outputs", "All recognized segments delivered in this sample"],
            ["Audio receipt p95", "4.49s alternating; 4.79s overlap", "After nominal input clip end; playback excluded"],
            ["Human meaning ratings", f"{q['mean']:.2f}/5; {q['score_4_or_5']}/{q['n']} scored >=4", "90 reviewed outputs from 30 unique fixtures"],
            ["Cross-language subset", f"{cross['mean']:.2f}/5; {cross['score_4_or_5']}/{cross['n']} scored >=4", "72 outputs; same-language outputs excluded"],
            ["Browser replay", "24/24 clips started and ended", "Actual player; cached audio and mocked socket"],
            ["Grouped fan-out", "540 pipelines for 720 recipient deliveries", "25% fewer logical translation/TTS invocations"],
        ]),
        paragraph("Conclusion: the tested architecture delivered complete two-speaker results, with strong but non-uniform meaning ratings. Code-mixed and Telugu inputs expose the most important semantic issues. Browser FIFO queueing is a remaining user-perceived latency bottleneck. These findings support a well-evidenced portfolio project, not a production reliability or universal translation-accuracy claim. [S01-S06]"))

    section("Test Scope and Methodology",
        table(["Test layer", "Design", "Sample / boundary"], [
            ["Controlled benchmark", "Real Sarvam APIs; independent speaker streams; one Docker worker; localhost WebSockets", "30 cached synthetic fixtures; 90 trials; 180 input runs"],
            ["Conditions", "15 fixed fixture pairs; 3 repetitions; randomized/counterbalanced order; seed 20261012", "Alternating: 1s inter-clip gap. Overlap: second speaker starts 0.3s later"],
            ["Participants", "2 speaker participants plus 3 stable listen-only targets", "Target languages rotate across the 4 supported languages"],
            ["Human review", "Owner confirms bilingual human reviewers assessed text and listened to saved audio", "90 outputs: alternating condition, repetition 1 only"],
            ["Playback replay", "Cached WAV arrivals replayed through actual frontend FIFO media player", "12 cases; 24 clips; desktop and mobile-sized viewports"],
            ["Historical tests", "Single-speaker structured campaign, two smoke runs, short timeout regression soak", "Different protocols; not pooled with controlled results"],
        ]),
        paragraph("The benchmark ran on 12 October 2026 local time; this report and review-method confirmation are dated 13 October 2026. Sources contain 6 fixtures per category: English, Hindi, Tamil, Telugu and Tamil-English mix. PCM is mono 16kHz / 16-bit, paced in 100ms frames. Each trial waits for upstream end and translation-worker drain, then checks complete final transcript sequence sets and valid WAV payloads. Two diagnostic trials are excluded from measured results. [S01, S02]"),
        paragraph("Configured models: saaras:v3-realtime (auto language, codemix, fast stream), mayura:v1 translation and bulbul:v3 synthesis (kavitha, 16kHz WAV). VAD settings: 500ms silence, threshold 0.3, 250ms minimum speech. These settings identify this experiment; they are not claimed to be optimal."))

    section("Delivery, Completeness and Concurrency",
        table(["Check", "Alternating", "Overlap", "Overall"], [
            ["Measured trials", "45/45", "45/45", "90/90"],
            ["Complete input runs", "90/90", "90/90", "180/180"],
            ["Stable-listener checks", "270/270", "270/270", "540/540"],
            ["Failed measured trials", "0", "0", "0"],
            ["Retries / replacements", "0", "0", "0 at measured-trial level"],
            ["Invalid / duplicate WAVs", "0", "0", "0"],
        ]),
        paragraph("Provider end counts match collected transcript counts for 180/180 speaker sessions; 180/180 fixture transmissions are complete. Independent evidence validation passed checks of manifest/build hashes, fixture/event/WAV hashes, secret-field exclusion, provider-end counts, output sequence completeness and recomputed percentiles. [S03]"),
        paragraph("Delivery means every final recognized segment had a matching translated text and valid audio payload for every stable listener. It does not mean the transcript or translation was semantically correct. The human-reviewed incomplete translations illustrate why successful transport and successful meaning preservation must remain separate."),
        paragraph("The two-speaker model keeps streams and workers independent; it does not separate voices mixed into one microphone. The campaign observes one utterance per speaker session and is not sustained-conversation backpressure, multi-room capacity or more-than-two-speaker stress testing. No measured trial replacements occurred; internal SDK/HTTP retries were not separately logged."),
        paragraph("Both predeclared exploratory targets were met: at least 95% complete delivery and overall post-clip receipt p95 no greater than 5s in each condition. The descriptive 90-input Wilson interval is 95.91%-100%, but repetitions share fixtures and are dependent, so this is not a production SLA confidence guarantee."))

    section("Latency and Overlap Cost",
        table(["Timing boundary", "Alternating p50 / p95", "Overlap p50 / p95"], [
            ["Input start to first audio receipt", "7.304s / 8.951s", "7.325s / 9.148s"],
            ["Clip end to last audio receipt", "3.564s / 4.488s", "3.677s / 4.790s"],
            ["Threshold signal end to last receipt", "3.823s / 4.748s", "3.969s / 4.923s"],
        ]),
        paragraph("Each condition has 270 target observations. Percentiles use nearest rank: sorted index ceil(p*n)-1. Nominal clip end can include trailing silence; threshold signal end is the final sample above amplitude 400, not human-labelled speech end. Input-start timing includes speaking duration. Capture, public-tunnel/WAN transit and actual playback start are excluded. [S01, S02]"),
        table(["Server stage", "Alternating p50 / p95", "Overlap p50 / p95"], [[label, f"{a[field+'_p50']} / {a[field+'_p95']} ms", f"{b[field+'_p50']} / {b[field+'_p95']} ms"] for label, field in [("STT", "stt_ms"), ("Worker queue", "queue_ms"), ("Translation", "translate_ms"), ("TTS", "tts_ms")] for a, b in [(all_summaries[0], all_summaries[1])]]),
        paragraph("STT stage timing runs from VAD speech-start to committed transcript and includes speaking duration, not isolated inference time. Do not sum stage percentiles or add playback-replay percentiles to receipt percentiles."),
        paragraph("Matched repetitions/targets were averaged within fixture. Mean paired overlap-minus-alternating post-clip delay was +129.90ms. The 2,000-resample fixture bootstrap 95% interval is +26.81 to +240.53ms; grouping the two fixtures sharing each trial into 15 pair blocks gives +2.28 to +276.59ms. This is a small positive overhead in this dataset, not zero overlap penalty or a general-network estimate."))

    section("Language-Specific Timing and Recognition",
        table(["Source", "Alt. p50 / p95", "Overlap p50 / p95", "WER / CER"], [[LABELS[a["source_category"]], f"{a['last_audio_after_clip_end_ms_p50']/1000:.3f}s / {a['last_audio_after_clip_end_ms_p95']/1000:.3f}s", f"{b['last_audio_after_clip_end_ms_p50']/1000:.3f}s / {b['last_audio_after_clip_end_ms_p95']/1000:.3f}s", f"{100*a['wer']:.2f}% / {100*a['cer']:.2f}%"] for a in summary["summaries"] if a["condition"] == "alternating" and a["source_category"] != "ALL" for b in summary["summaries"] if b["condition"] == "overlap" and b["source_category"] == a["source_category"]]),
        paragraph("Each source category contributes 18 input runs and 54 target observations per condition. Overall normalized WER is 11.65% (72 word edits / 618 reference words per condition); CER is 8.01% (270 character edits / 3,369 reference characters). WER/CER are identical between alternating and overlap in this sample. They are script-comparison metrics, not translation accuracy. [S02]"),
        paragraph("The aggregate p95 meets 5s, but Hindi-source overlap p95 is 5.846s. Aggregation therefore hides a slower source subgroup. Source/target cells in the raw language-pair table provide additional descriptive detail; the sample is too small to rank languages universally."),
        paragraph("References are TTS input scripts, not independently human-transcribed source waveforms. Code-mix transliteration, word segmentation and TTS pronunciation can increase edit distance without equivalent semantic loss. Hindi presentation script changes and Tamil compound-word splitting are examples. Telugu te_3 also has reviewer-reported STT corruption propagated into translation. Do not convert 1-WER into translation accuracy or claim all reference mismatches are STT-only failures."))

    section("Controlled Measurement Distributions",
        {"kind": "image", "path": str(BENCH / "benchmark.png"), "caption": "Original controlled benchmark plots. Receipt ECDFs use 270 target observations per condition. The paired plot averages repetitions/targets within each of 30 fixtures. WER is script comparison, not translation quality. Complete-input bars show 90/90 input runs per condition."},
        paragraph("Reading the figure: the receipt ECDF rises more slowly for overlap, consistent with the +129.90ms paired mean overhead. The fixture-difference plot shows variation rather than a uniform overlap cost. Reference WER is unchanged between conditions. Complete-input counts remain equal despite concurrent speaker input."),
        paragraph("The figure visualizes already-tabulated automated evidence, not additional observations. It excludes playback timing and human-review scores. Related targets/repetitions must not be treated as independent speech samples. [S02]"))

    section("Bilingual Human Review: Overall Results",
        paragraph("The submitted sheet has 270 rows, all alternating-condition outputs across three repetitions. Exactly 90 have reviewer labels, reviewed status and valid 1-5 scores; 180 are unreviewed and excluded. The 270 overlap outputs from the controlled benchmark are not present in the submission and have no human ratings. All 30 unique controlled fixtures have three reviewed target outputs from repetition 1. [S04]"),
        table(["Score", "Interpretation used for reporting", "Count", "Share"], [[str(score), label, str(q["distribution"][str(score)]), f"{100*q['distribution'][str(score)]/q['n']:.2f}%"] for score, label in [(5, "All important meaning preserved"), (4, "Minor wording issue; meaning mostly preserved"), (3, "Noticeable error/omission; general idea understandable"), (2, "Major meaning error / incomplete translation"), (1, "Unusable or contradictory")]]),
        table(["Quality statistic", "Observed value"], [
            ["Arithmetic mean / median", f"{q['mean']:.4f} / 5.0 out of 5"],
            ["Score >=4 threshold share", f"{q['score_4_or_5']}/{q['n']} = {q['score_4_or_5_pct']:.2f}%"],
            ["Below-threshold outputs", f"{q['below_4']}/{q['n']} = {100*q['below_4']/q['n']:.2f}%"],
            ["All three targets scored >=4", f"{data['fixtures_all_targets_score_4_or_5']}/30 fixtures"],
            ["Fixtures with any non-perfect rating", f"{data['fixtures_with_any_score_below_5']}/30 fixtures"],
        ]),
        paragraph("The owner confirms bilingual humans reviewed text and listened to the saved audio. The sheet uses four language-labelled reviewer IDs, with 22 English, 22 Hindi, 24 Tamil and 22 Telugu rows; these labels do not establish four independently verified people or their qualifications. No review timestamps, blind-rating protocol, second ratings, inter-rater agreement or separate audio-intelligibility scores were supplied."),
        paragraph("A score of at least 4 is a descriptive reporting threshold, not an independently audited safety rule. Ratings are ordinal; the mean is a descriptive summary. Do not convert 4.62/5 into 92.4% accuracy. Related targets share a fixture, so 90 ratings are not 90 independent speech samples. No inferential human-quality interval is asserted."))

    section("Human Review: Language Coverage",
        table(["Source category", "Ratings", "Mean /5", "Score >=4", "Below 4"], [[LABELS[row["source_category"]], str(row["n"]), f"{row['mean']:.2f}", f"{row['score_4_or_5']}/{row['n']} ({row['score_4_or_5_pct']:.1f}%)", str(row["below_4"])] for row in data["by_source"]]),
        table(["Target language", "Ratings", "Mean /5", "Score >=4", "Below 4"], [[LABELS[row["target_language"][:2]], str(row["n"]), f"{row['mean']:.2f}", f"{row['score_4_or_5']}/{row['n']} ({row['score_4_or_5_pct']:.1f}%)", str(row["below_4"])] for row in data["by_target"]]),
        table(["Output subset", "Ratings", "Mean /5", "Score >=4"], [["Cross-language + mixed-source", str(cross["n"]), f"{cross['mean']:.3f}", f"{cross['score_4_or_5']}/{cross['n']} ({cross['score_4_or_5_pct']:.2f}%)"], ["Same-language monolingual", str(same["n"]), f"{same['mean']:.3f}", f"{same['score_4_or_5']}/{same['n']} ({same['score_4_or_5_pct']:.2f}%)"]]),
        paragraph("The headline 90-output result includes 18 monolingual same-language outputs. The 72-output cross-language subset includes every Tamil-English mixed-source result, since a mixed source has no single same-language target. Mixed-source to Tamil/English is partly language normalization rather than a clean monolingual cross-language task. These subset rules are explicit to prevent inflated translation claims."),
        paragraph("Source totals are balanced at 18 ratings each; targets are not exactly balanced (22/22/24/22). Source-target cells contain only 3-6 ratings. Apparent differences may reflect the six selected sentences, target difficulty and reviewer calibration; they do not establish a model-wide language ranking. [S04]"))

    section("Human Review: Source-to-Target Detail",
        table(["Source", "Target", "n", "Mean /5", "Score >=4"], [[LABELS[row["source_category"]], LABELS[row["target_language"][:2]], str(row["n"]), f"{row['mean']:.2f}", f"{row['score_4_or_5']}/{row['n']} ({row['score_4_or_5_pct']:.1f}%)"] for row in data["by_source_target"]]),
        paragraph("These 20 cells describe the available 90 first-repetition ratings, not all possible utterances or all overlap translations. English-source and Hindi-source ratings are high in this small sample; Tamil-English mixed-source and Telugu-source examples account for most reported below-threshold outputs. Full original text, translated text, corrections and notes are retained in the immutable review snapshot and reviewed-observations appendix. [S04]"))

    issue_specs = [
        ("mix_2", "Semantic role shift", "Busy attributed to the person rather than the route. Review the source phrasing and preserve who/what the modifier describes.", "Meaning attribution; ambiguous source/context"),
        ("mix_7", "Incomplete content", "Tamil and English target translations were rated incomplete / cut off. Valid WAV delivery does not prove full semantic content.", "Trace source transcription, translation and speech termination before assigning cause"),
        ("te_10", "Travel-direction mistranslation", "Hindi and Telugu outputs were described as dropping off / picking up to take home instead of the intended meaning.", "Translation/source interpretation; not a demonstrated transport failure"),
        ("te_3", "STT error propagation", "The Telugu deadline expression was misrecognized; reviewer notes say the corrupted phrase propagated to all three targets.", "Reviewer-attributed upstream recognition issue"),
        ("ta_2", "STT error propagation", "Reviewer notes say the Tamil time/context expression was misrecognized, changing the intended context to 'here'.",
         "Reviewer-attributed upstream issue; verify exact waveform/transcript alignment"),
        ("te_4", "Relationship detail changed", "Younger brother became older brother in Tamil. Rated 4, but the relationship fact changed.", "A high score alone does not exclude important factual errors"),
        ("mix_4", "Output repetition", "Tamil output repeated the quantity marker ('a 1' in the review description).", "Minor fluency/formatting issue"),
        ("en_13", "Meaning nuance", "Telugu wording changed 'moved' to 'postponed'.", "Scheduling nuance; needs context-sensitive review"),
        ("te_13", "Awkward phrasing", "English output was described as 'changed at 3 o'clock'.", "Minor fluency issue; clarify temporal phrasing"),
    ]
    issues = []
    for fixture, label, explanation, attribution in issue_specs:
        affected = [row for row in reviewed if row["fixture_id"] == fixture and int(row["meaning_preservation_1_to_5"]) < 5]
        issues.append({"fixture": fixture, "label": label, "n": len(affected), "targets": ", ".join(row["target_language"][:2] for row in affected), "scores": ", ".join(row["meaning_preservation_1_to_5"] for row in affected), "explanation": explanation, "attribution": attribution})
    section("Qualitative Error Catalogue",
        paragraph("Seventeen outputs received a non-perfect score across nine fixtures: 4 major/incomplete outputs scored 2, 9 noticeable-error outputs scored 3 and 4 minor-issue outputs scored 4. The catalogue below groups repeated target-level notes by fixture, without treating three affected targets as three independent underlying incidents."),
        table(["Fixture / issue", "Affected targets; scores", "Reviewer finding"], [[row["fixture"] + ": " + row["label"], row["targets"] + "; " + row["scores"], row["explanation"]] for row in issues]),
        paragraph("Six below-threshold outputs across te_3 and ta_2 were explicitly attributed to upstream STT errors in reviewer notes; the other seven below-threshold outputs span semantic role shift, incomplete translation and travel-direction mistranslation. This is note-based attribution, not experimentally isolated root-cause proof. All these outputs passed transport/WAV completeness checks."),
        paragraph("The Tamil younger/older-brother example scored 4, illustrating that the >=4 threshold cannot stand in for a critical-factual-error rate. No dedicated critical-error field or formal severity audit exists; therefore no critical-error percentage is reported. The six supplied correction fields for te_3/ta_2 are preserved as submitted, not treated as an independently verified replacement transcript. [S04]"))

    replay = json.loads((BENCH / "playback_replay.json").read_text())
    replay_rows = read_csv(BENCH / "playback_replay.csv")
    replay_table = []
    for width in (1440, 390):
        for condition in ("alternating", "overlap"):
            selected = [row for row in replay_rows if int(row["viewport_width"]) == width and row["condition"] == condition]
            values = sorted(float(row["receive_to_playback_ms"]) for row in selected)
            replay_table.append(["Desktop" if width == 1440 else "Mobile-sized", condition, str(len(values)), f"{values[math.ceil(.5*len(values))-1]:.2f}ms", f"{values[math.ceil(.95*len(values))-1]:.2f}ms"])
    section("Playback and Interface Validation",
        table(["Viewport", "Condition", "Clips", "Receive-to-play p50", "p95"], replay_table),
        paragraph(f"Replay covered {len(replay['cases'])} cases at 1440x1000 and 390x844. All 24 clips started and ended exactly once; no media errors, case errors or horizontal overflow were recorded, and FIFO order was checked. Edge/Chromium version: {replay['browser']}. The small mobile viewport is not physical mobile-hardware validation. [S05]"),
        paragraph("Overlap receive-to-playing p95 was 4.294s on desktop and 4.522s in the mobile-sized viewport. Queueing preserves complete clips but can delay the second speaker's translation while the first is playing. This is the most visible latency trade-off in the evidence; p95 receipt below 5s does not imply sub-5s human-heard translation."),
        paragraph("Frontend regression coverage includes visible microphone icons; manual mute/unmute; actual local audio-track disable while the playback guard is enabled; automatic recovery without overriding manual mute; continued PCM capture when the guard is disabled; queued two-speaker playback; and desktop/mobile layout checks. This is automated component coverage, not evidence that every browser/physical microphone behaves identically. [S07]"),
        paragraph("The owner previously reported the basic live features working. No structured real-laptop acceptance log, human microphone-latency trace or participant satisfaction survey was submitted, so those are recorded as informal feedback rather than quantified usability results."))

    section("Historical Tests and Timeout Regression",
        table(["Campaign", "Result", "Interpretation"], [
            ["Structured developmental campaign (9 Oct)", "92/92 fixtures; 368/368 final target receipts; 2 controlled retries", "Input-start first receipt p50 7.233s, p95 9.700s"],
            ["Five-fixture pilot", "Preparation data reused by the structured campaign", "Not additional independent evidence"],
            ["Two earlier room smoke runs", "8 sessions per file; 32 audio events per file", "16 sequential session records; not simultaneous-room scale"],
            ["Timeout-fix real-provider soak", "215.203s; both speakers translated again after 210s", "Exceeded reported 2-3 minute failure window; not 30-minute endurance"],
            ["Backend/harness regression suite", "32 tests passed on Windows again on 13 Oct; prior Linux Docker check also passed", "Mocked/offline regression evidence, not paid live delivery"],
        ]),
        paragraph("The structured campaign used one speaker and four listeners, with a different timing boundary (speaker send-start to first audio receipt). The first 14 successes lacked the later explicit 0.8s silence tail, implementation was hardened during the campaign, and two failed cases were retried. Its zero final missing outputs are not a zero-first-attempt-failure claim. Do not pool its timings with the later counterbalanced benchmark or compute a latency improvement percentage. [S08]"),
        paragraph("The timeout incident was traced to an application SESSION_SECONDS=60 timer, despite unlimited credit allowances. The fix separates the spending allowance from the session timer and reports explicit expiry. The owner chose a 30-minute limit. Saved soak evidence shows listener audio from both speakers initially at 5.453s/5.797s and again at 214.859s/215.094s; no error events were reported and the run ended cleanly. [S09]"),
        paragraph("The 13 October Windows recheck completed 32 tests in 15.249s with normal filesystem access. An initial sandbox-restricted attempt encountered temporary-file errors and stalled; it was stopped, and the unrestricted offline rerun passed. A Starlette/httpx deprecation warning remains non-fatal; no dependency upgrade was performed for this reporting task."),
        paragraph("Earlier smoke files and developmental results are retained for completeness, but do not strengthen the independent-sample denominator of the controlled study. The originally credit-exhausted structured attempt is historical only; the later 92-fixture campaign completed it. The full 30-minute endurance test remains intentionally unperformed."))

    consumption = json.loads((BENCH / "consumption.json").read_text())
    section("Resource Usage and Fan-Out Efficiency",
        table(["Measured component", "Quantity", "Archived rate", "Computed INR, ex GST"], [
            ["Provider audio duration", "1,129s over 180 sessions", "30 per hour", f"{1129/3600*30:.3f}"],
            ["Successful translation input", "23,508 characters", "20 per 10,000 characters", f"{23508/10000*20:.3f}"],
            ["Observed TTS input", "24,769 characters", "30 per 10,000 characters", f"{24769/10000*30:.3f}"],
            ["Measured processing estimate", "Diagnostics excluded", "Rates archived on 12 Oct 2026", f"{consumption['estimated_inr_excluding_gst']:.3f}"],
        ]),
        paragraph("The controlled benchmark's conservative reservation ledger, including diagnostic activity, estimates INR 215.861 ex GST because STT reserves whole bounded sessions. The earlier structured campaign reservation estimate is INR 112.111 ex GST. These different scopes are not actual account billing receipts; they are not summed into a lifetime spend claim. Internal failed/retried HTTP requests, fixture generation and other development calls may add charges. No provider balance was queried. [S06, S08]"),
        paragraph("Observed grouping: 540 successful distinct-target translation/TTS pipelines delivered 720 recipient-level audio outputs (540 stable-listener outputs plus 180 speaker-recipient outputs). A per-recipient design would invoke each pipeline 720 times for these observed recipients, so the computed logical-call reduction is (720-540)/720 = 25.00%, for translation and TTS separately."),
        paragraph("This is architectural reuse quantified from observed recipient lists, not an A/B billing, speed or total-cost experiment. It does not mean 25% lower total Sarvam charges. The current compilation, human-rating aggregation and document rendering reuse cached evidence and make no live API calls, so they consume no additional Sarvam credits."))

    section("Data Integrity, Boundaries and Remaining Gaps",
        table(["Submission integrity check", "Result"], [
            ["Unique keys / benchmark matches", f"{data['matched_rows']}/{data['submission_rows']} matched; no duplicate keys"],
            ["Reviewed / unreviewed submitted", f"{data['reviewed_rows']} / {data['unreviewed_submission_rows']}"],
            ["Unsubmitted benchmark outputs", f"{data['unsubmitted_benchmark_rows']} overlap outputs; no human score inferred"],
            ["Identity, text and WAV hashes", "Preserved; only human fields and formatting differences accepted"],
            ["Receipt-time export rounding", f"{data['rounding_changes'].get('audio_received_ms',0)} cells; max 0.005ms difference"],
            ["WAV-duration export rounding", f"{data['rounding_changes'].get('wav_duration_s',0)} cells; max 0.0000005s difference"],
        ]),
        paragraph("All quantitative performance calculations use original benchmark data, not the rounded review export. Original benchmark native_review.csv remains unchanged and historically unreviewed; the new submission is archived separately with its original bytes and SHA-256. Ratings are joined by trial, fixture, speaker, target language, receiver and sequence, not by row position."),
        paragraph("Evidence limitations: synthetic voice and only six fixtures per source; shared development laptop; no independent source-waveform transcript audit; reviewer credentials/blinding/calibration unrecorded; one score per output; qualitative audio listening without a distinct intelligibility metric; no overlap human reviews; and no production-scale or noisy-human-speech study."),
        paragraph("Untested boundaries: full 30-minute endurance, sustained-conversation queue saturation, WAN/tunnel end-to-end timing, physical mobile hardware, simultaneous multi-room throughput and more than two unmuted microphones. Browser AEC/noise suppression is requested, but acoustic source separation or Zoom/Meet-equivalent proprietary audio processing is not implemented or demonstrated."),
        paragraph("The max_frame_lateness_ms value of zero in the controlled harness measures next-frame deadline overrun after send, not absolute dispatch jitter. It cannot support a zero-jitter claim. Reviews of generated audio also do not constitute field validation of actual human accents or microphone acoustics."))

    section("Findings, Priorities and Portfolio Claims",
        table(["Priority", "Evidence-led next work", "Verification to use"], [
            ["1. Semantic correctness", "Investigate mix_2, mix_7, te_10, te_3 and ta_2; separate source/STT/translation causes", "Corrected-source probes, bilingual re-review; preserve this baseline"],
            ["2. Playback latency", "Reduce long overlap queues without cutting off meaning", "Measure actual receipt-to-playing and complete content on matched inputs"],
            ["3. Review robustness", "Add critical factual-error flags and second ratings on disagreement-prone cases", "Report agreement and original/adjudicated scores separately"],
            ["4. Real speech", "Add diverse consented human recordings and short two-laptop acceptance notes", "New held-out set, browser/network context and human-labelled speech end"],
        ]),
        paragraph("Overall judgement: a functional and benchmarked multilingual conferencing prototype with demonstrated independent two-speaker handling, complete recognized-output delivery and reproducible evidence. Human review adds strong meaning-preservation ratings while making semantic failure modes visible. No new paid infrastructure or architectural expansion is necessary solely to make it resume-worthy."),
        paragraph("Defensible wording: 'Built a Dockerized, WebSocket-based speech translation app across four languages with two concurrent speaker streams; validated 180/180 controlled inputs and 540/540 listener audio outputs across alternating and overlapping speech.'"),
        paragraph(f"Defensible wording: 'Evaluated 90 multilingual outputs using bilingual human text/audio review, averaging {q['mean']:.2f}/5 for meaning preservation; {q['score_4_or_5_pct']:.1f}% scored at least 4 (30 synthetic source fixtures, alternating condition).' For a strictly cross-language claim use {cross['mean']:.2f}/5 and {cross['score_4_or_5']}/{cross['n']} outputs instead."),
        paragraph("Defensible wording: 'Observed post-clip audio-receipt p95 of 4.49s alternating and 4.79s overlapping, excluding playback; target-language fan-out used 25% fewer logical translation/TTS invocations than a per-recipient counterfactual in the measured trials.'"),
        paragraph("Avoid: 100% production reliability; 85.6% universal translation accuracy; 92.4% accuracy from a 4.62 score; sub-5s microphone-to-playback latency; zero overlap cost; 30-minute endurance passed; native-speaker credentials verified; or 25% measured total billing savings."))

    section("Evidence Register and Reproduction",
        table(["ID", "Source in this project", "Use"], [
            ["S01", "docs/CONTROLLED-BENCHMARK-PLAN.md; controlled_20261012/REPORT.md; metadata.json; manifest.json", "Protocol, settings, environment and limitations"],
            ["S02", "controlled_20261012/summary.json; inputs.csv; targets.csv; paired.csv; language_pairs.csv", "Controlled measurement and paired analysis"],
            ["S03", "controlled_20261012/verification.json; events/; trials/; harness_measured.py", "Sequence/hash validation and exact measured harness"],
            ["S04", "reviewed_20261013/human_review_submission.csv; reviewed_observations.csv; human_review_summary.json", "Submitted scores, original notes and review audit"],
            ["S05", "controlled_20261012/playback_replay.json; playback_replay.csv", "Cached frontend replay observations"],
            ["S06", "controlled_20261012/consumption.json; Sarvam archived rate source", "Usage estimate, not actual billed charges"],
            ["S07", "tests/test_app.py; test_local_demo.py; test_controlled_benchmark.py; browser.cjs", "Regression coverage and prior test results"],
            ["S08", "structured_test_campaign_*: report.md, metadata.json, summary.csv; two load_test_*.json files", "Historical developmental and smoke tests"],
            ["S09", "docs/AUDIO-CONCURRENCY.md; logs/local-demo/live-soak-result.json", "Timeout diagnosis and short real-provider soak"],
        ]),
        paragraph("Unless prefixed otherwise, result sources live under loadtest/results/. The reviewed submission came from the owner's Downloads/Test - Sheet1.csv; the archived snapshot is byte-identical. Review-method confirmation is the owner's 13 October 2026 chat statement that bilingual humans assessed the text and listened to audio."),
        paragraph("Rebuild the report offline: python scripts/build_testing_report.py --review loadtest/results/reviewed_20261013/human_review_submission.csv --skip-pdf. This uses only the Python standard library and checks observation joins, scoring completeness, rounding bounds and raw latency percentiles. It writes the Markdown report, reviewed-observation appendix and JSON audit, preserving the source snapshot. Optional PDF export needs reportlab and Pillow; omit --skip-pdf to include it."),
        paragraph("Full cached benchmark validator: .venv/Scripts/python.exe loadtest/verify_controlled.py. It requires ignored cached fixture/WAV runtime files. Original source/fixture audio is not embedded in the report. The review snapshot preserves Unicode text and corrections; PDF error descriptions are English summaries for legibility."),
        paragraph("Review submission SHA-256: " + data["review_source_sha256"]),
        paragraph("Checksums, reviewer counts and import audit: human_review_summary.json. Measured code/image hashes: controlled metadata. The original study is unchanged."))
    return parts, issues


def markdown(sections, reviewed):
    lines = ["# Sarvam Interpreter: Consolidated Testing Report", "", f"Compiled {REPORT_DATE}. Controlled measurement: 2026-10-12. Human review supplied by the project owner.", ""]
    for section in sections:
        lines.extend(["## " + section["title"], ""])
        for block in section["blocks"]:
            if block["kind"] == "paragraph":
                lines.extend([block["text"], ""])
            elif block["kind"] == "table":
                clean = lambda value: str(value).replace("|", "\\|").replace("\n", " ")
                lines.append("| " + " | ".join(block["headers"]) + " |")
                lines.append("| " + " | ".join("---" for _ in block["headers"]) + " |")
                lines.extend("| " + " | ".join(clean(cell) for cell in row) + " |" for row in block["rows"])
                lines.append("")
            elif block["kind"] == "image":
                lines.extend(["![Controlled measurement plots](../loadtest/results/controlled_20261012/benchmark.png)", "", block["caption"], ""])
    lines.extend(["## Appendix: Original Non-Perfect Review Notes", "", "Original submitted wording is preserved below; score-5 notes are preserved in the full review CSV.", "", "| Fixture | Target | Score | Correction | Reviewer note |", "| --- | --- | --- | --- | --- |"])
    for row in reviewed:
        if int(row["meaning_preservation_1_to_5"]) < 5:
            lines.append("| " + " | ".join(row[field].replace("|", "\\|") for field in ["fixture_id", "target_language", "meaning_preservation_1_to_5", "correction", "notes"]) + " |")
    return "\n".join(lines) + "\n"


def build_pdf(sections):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, KeepTogether
    from PIL import Image as PILImage

    ink = colors.HexColor("#17272B")
    accent = colors.HexColor("#16776C")
    muted = colors.HexColor("#526368")
    width = A4[0] - 92
    styles = {
        "title": ParagraphStyle("Title", fontName="Helvetica-Bold", fontSize=23, leading=28, textColor=ink, spaceAfter=12),
        "heading": ParagraphStyle("Heading", fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=ink, spaceAfter=13),
        "body": ParagraphStyle("Body", fontName="Helvetica", fontSize=10, leading=14, textColor=ink, spaceAfter=11),
        "cell": ParagraphStyle("Cell", fontName="Helvetica", fontSize=8.5, leading=11.5, textColor=ink),
        "headcell": ParagraphStyle("HeadCell", fontName="Helvetica-Bold", fontSize=8.5, leading=11.5, textColor=colors.white),
        "caption": ParagraphStyle("Caption", fontName="Helvetica", fontSize=8.4, leading=11.5, textColor=muted, spaceAfter=10),
        "eyebrow": ParagraphStyle("Eyebrow", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=accent, spaceAfter=8),
    }
    story = []
    for index, section in enumerate(sections):
        if index:
            story.append(PageBreak())
        if not index:
            story.append(Paragraph("ENGINEERING EVALUATION / 13 OCTOBER 2026", styles["eyebrow"]))
            story.append(Paragraph("Sarvam Interpreter", styles["title"]))
            story.append(Paragraph("Consolidated Testing Report", styles["heading"]))
        else:
            story.append(Paragraph(f"{index+1:02d} / {section['title']}", styles["heading"]))
        for block in section["blocks"]:
            kind = block["kind"]
            if kind == "paragraph":
                story.append(Paragraph(escape(block["text"]), styles["body"]))
            elif kind == "table":
                n = len(block["headers"])
                rows = [[Paragraph(escape(str(cell)), styles["headcell"]) for cell in block["headers"]]]
                rows.extend([[Paragraph(escape(str(cell)), styles["cell"]) for cell in row] for row in block["rows"]])
                proportions = {2: [0.36, 0.64], 3: [0.26, 0.32, 0.42], 4: [0.32, 0.23, 0.23, 0.22], 5: [0.28, 0.18, 0.13, 0.22, 0.19]}[n]
                grid = Table(rows, colWidths=[width * proportion for proportion in proportions], repeatRows=1, hAlign="LEFT")
                grid.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), accent),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F0F5F4"), colors.white]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LINEBELOW", (0, 0), (-1, 0), .5, accent),
                ]))
                story.extend([grid, Spacer(1, 14)])
            elif kind == "image":
                w, h = PILImage.open(block["path"]).size
                image = Image(block["path"], width=width, height=width * h / w)
                story.append(KeepTogether([image, Spacer(1, 5), Paragraph(escape(block["caption"]), styles["caption"])]))

    def page(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#CEDBD8"))
        canvas.line(46, A4[1]-35, A4[0]-46, A4[1]-35)
        canvas.setFillColor(muted)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(46, A4[1]-26, "SARVAM INTERPRETER / TESTING EVIDENCE")
        canvas.drawString(46, 28, "Controlled synthetic benchmark + bilingual human review | 13 Oct 2026")
        canvas.drawRightString(A4[0]-46, 28, str(document.page))
        canvas.restoreState()

    document = SimpleDocTemplate(str(PDF), pagesize=A4, rightMargin=46, leftMargin=46, topMargin=51, bottomMargin=48,
                                 title="Sarvam Interpreter - Consolidated Testing Report", author="Sarvam project",
                                 subject="Controlled quantitative measurements and bilingual human qualitative review")
    document.build(story, onFirstPage=page, onLaterPages=page)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--skip-pdf", action="store_true", help="Build Markdown and CSV/JSON evidence using only the standard library")
    args = parser.parse_args()
    data, submitted, reviewed, summary = analyze(args.review)
    sections, issues = make_sections(data, reviewed, summary)
    OUT.mkdir(parents=True, exist_ok=True)
    PDF.parent.mkdir(parents=True, exist_ok=True)
    archived = OUT / "human_review_submission.csv"
    if archived.exists():
        assert digest(archived) == digest(args.review), "Refusing to replace a different immutable review submission"
    else:
        shutil.copyfile(args.review, archived)
    data["issue_catalogue"] = issues
    (OUT / "human_review_summary.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (OUT / "reviewed_observations.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        fields = list(submitted[0]) + ["output_subset", "score_4_or_5"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in reviewed:
            writer.writerow({**row, "output_subset": "same_language" if row["source_category"] == row["target_language"][:2] else "cross_language_or_mixed", "score_4_or_5": int(row["meaning_preservation_1_to_5"]) >= 4})
    MD.write_text(markdown(sections, reviewed), encoding="utf-8")
    if not args.skip_pdf:
        build_pdf(sections)
    print(json.dumps({"pdf": None if args.skip_pdf else str(PDF), "markdown": str(MD), "all_reviewed": data["all_reviewed"], "cross_language": data["cross_language"], "by_source": data["by_source"], "by_target": data["by_target"]}, indent=2))


if __name__ == "__main__":
    main()
