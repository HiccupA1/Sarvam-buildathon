# Controlled Benchmark

Protocol: `../docs/CONTROLLED-BENCHMARK-PLAN.md`. Results:
`results/controlled_20261012/REPORT.md`. Historical results use different timing
definitions and must not be used to claim a latency improvement.

## Reproduce

From the repository root, with Docker running and a local `.env` Sarvam key:

```powershell
docker build -t sarvam-interpreter:controlled-benchmark .
.venv\Scripts\python.exe loadtest\controlled_benchmark.py
# Plan only above. The next command is billable.
.venv\Scripts\python.exe -u loadtest\controlled_benchmark.py --live --output loadtest/results/new_controlled_run
.venv\Scripts\python.exe loadtest\analyze_controlled.py loadtest/results/new_controlled_run
```

The isolated service binds to loopback port 8782, has a private generated invite
and cleans up its own labeled container. Existing demo containers/ledger stay
unchanged. Key files are ignored and removed after startup. Do not print Docker
environment inspection output.

Seeded manifests/trial checkpoints are immutable. The same directory resumes
missing trials, not failed trials; build/harness hashes must match. Use a new
directory for changed code/protocol. Every final segment is correlated and
WAV-validated; first audio alone is insufficient. No automatic trial retries.

The CSV/JSON analyzer uses the Python standard library. Optional plots require
`pip install -r loadtest/requirements-analysis.txt`, then run
`python loadtest/plot_controlled.py <results-directory>`.

`replay_playback.cjs` uses fixed pairs 1-3 / repetition 1 from the 20261012
campaign, replaying cached real WAVs at measured receipt offsets. Requires
Playwright and Edge; opens an offline localhost backend on port 8783, no API
calls. Desktop/mobile viewport tests verify every clip starts and ends once,
FIFO order, no media errors and no horizontal overflow. Receive-to-playing
delay is not live end-to-end latency. Local WAVs: ignored
`logs/controlled-benchmark/audio/`. Raw JSONL events omit audio base64.

Synthetic voice/reference bias, no native semantic review, loopback network,
two independent speaker streams only, no endurance run. Do not turn WER into
an unsupported translation-accuracy percentage.
