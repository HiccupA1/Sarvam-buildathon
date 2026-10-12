# Controlled speech benchmark plan

Protocol date: 2026-10-12. Written before measured live trials.

## Question and Scope

Does two-speaker input overlap change latency, transcript fidelity or complete
recognized-segment delivery compared with alternating input on this build?
Measure the real Docker backend and real Sarvam STT/translation/TTS over local
loopback. This is not an internet, physical-microphone, acoustic separation or
human translation-quality benchmark. No 30-minute endurance test.

## Dataset and Design

- Reuse PCM16 mono 16 kHz Sarvam-generated fixtures; no new fixture TTS.
- Select six fixtures from each of English, Hindi, Tamil, Telugu and
  Tamil-English code-mix without replacement, seed 20261012: 30 unique inputs.
- Save reference text, SHA-256, duration, signal bounds and amplitude diagnostics
  in a manifest. Non-English text is model-generated, not native-speaker vetted.
- Shuffle into 15 fixed pairs. Run alternating and overlap conditions, three
  repetitions: 90 trials / 180 utterance runs.
- Alternating: B starts one second after A's clip finishes. Overlap: B starts
  0.3 seconds after A starts. Independent PCM streams, never mixed input PCM.
- Seeded counterbalanced condition order. Shuffle pair order per repetition.
  Keep listener languages matched for both conditions. Rotate three distinct
  listener languages across four supported languages. Speakers both hear the
  first listener's language.
- Five participants: two speakers, three listen-only recipients. Primary
  delivery denominator concerns the three stable listeners. Speaker-to-speaker
  delivery is recorded separately, not treated as a stable listener.
- One excluded diagnostic pair per condition before measured trials. Preserve
  measured failures; no automatic retries or replacement.
- At least 12 seconds between trial starts; do not hammer the provider.

## Automation and Completeness

Separate invite-protected Docker backend on 127.0.0.1:8782, no public tunnel.
Keys stay in ignored local configuration. Existing demo/ledger remain untouched.
Use a separate benchmark ledger, backend metrics and sanitized JSONL events.

Stream approximately 100 ms frames, send silence through the clip boundary,
then explicitly end both streams. Await `stream_drained`, emitted only after
upstream reception finishes and the ordered translation worker drains. Collect
all final segments, not just first audio. Correlate speaker/sequence/target.
Validate WAV headers, nonzero samples, duration and payload hash. Do not record
audio base64, access tokens or resume tokens in raw events. Keep local cached
WAVs for browser replay.

Success requires a final transcript, clean stream drain, no relevant errors
and valid audio for every final segment at every stable listener. This is
recognized-segment delivery completeness, not semantic/word-complete
translation. Measure reference omissions separately with WER/CER. Partial
results and timeouts remain failures.

## Measurements

- Input-start, clip-end and last-above-threshold sample to first/last audio
  receipt. Signal bounds: absolute PCM amplitude >400. Last-audio receipt is
  final segment payload arrival, not playback finish.
- STT connection and server STT/queue/translation/TTS stages, valid WAV duration.
  Server `total_ms` is not complete end-to-end playback latency.
- Per-segment delivery, duplicates, missing outputs, first-attempt failures,
  sample counts and observed input overlap.
- Normalized WER and Unicode-codepoint CER across concatenated final transcripts.
  Normalize punctuation/case; disclose code-mix/transliteration/synthetic biases.
  Do not invent an automatic semantic translation-accuracy score.
- Distinct-language pipeline calls and recipient fan-out against a hypothetical
  call-per-recipient strategy: architectural savings, not a measured speedup.
- Audio-sent duration, reserved characters and provider-reported session-end
  audio when available. Cost remains estimated without billing reconciliation.

## Analysis Fixed Before Measurement

Nearest-rank p50/p95, with sample counts, by condition and source category.
Paired per-fixture differences after averaging repetitions; bootstrap 30
fixture-level differences with fixed seed for a 95% interval of mean difference.
Targets/repetitions are not independent fixtures. Disclose missing pairs;
never fill missing values with zero. Wilson success intervals are descriptive,
not a production guarantee because repeated inputs are dependent.

Analysis implementation clarification, added while the campaign was running:
match repetition identifiers before computing per-fixture differences. Retain
the specified fixture bootstrap, and additionally report a 15-pair-block
bootstrap because two fixtures share each trial. This exposes dependency
rather than choosing a more favourable interval. Both intervals are retained.

Exploratory targets: >=95% complete recognized-segment delivery and p95
last-audio receipt <=5 seconds after clip end in each condition. Report whether
these predeclared targets were met. Never adjust thresholds to flatter results.

## Playback and Deliverables

Run desktop/mobile frontend regressions offline. Replay a fixed subset of
cached real TTS responses through the browser playback queue at measured arrival
offsets. Record actual receive-to-playback delays and errors. Label as cached
response replay, not live capture-to-playback or additional paid trials.

Deliver manifest, measured checkpoints, raw JSONL events, observation and
aggregate CSV/JSON, paired comparisons, plots, native-review worksheet and a
detailed Markdown report. Record build/harness hashes, environment, definitions,
failures and limitations. Do not merge historical data or remove inconvenient
results. Skip endurance claims. Draft only evidence-backed resume statements.

## Skills

`find-skills` found `grafana/skills@k6`: CLI reported 4.3K installs; official
repository showed 282 stars. Consulted validation, response assertions, pacing
and percentile/error reporting guidance:
https://github.com/grafana/skills/tree/main/skills/grafana-k6/k6
Keep the existing Python WebSocket stack for PCM/correlation, no k6 Cloud or
subscription. Bundled spreadsheet guidance informs traceable CSVs and explicit
missing-data handling.
