# Sarvam Live Speech Interpreter

A browser-based interpreter that lets people speak naturally while each listener
hears their preferred language. It supports English, Hindi, Tamil, and Telugu,
including language switching and code-mixed input, in rooms of up to five people.

For example, a participant can choose to hear Tamil while speaking English, and
another participant can receive that speech as Hindi text and audio. A listening
preference never locks someone into a speaking language.

- [Evaluation and human review](docs/TESTING-REPORT.md)
- [Hosted interface preview](https://sarvam-interpreter-preview.onrender.com/)
  — live processing is disabled; use the local setup below for actual speech.

## Competition story and my contribution

I proposed this idea for a campus competition. The Sarvam team recognized the
idea and awarded Sarvam credits to continue building it beyond the competition.

This is a solo project built with AI-assisted coding and real Sarvam API
integration. I defined the product idea, directed the AI-assisted implementation,
and worked through integration, testing, and evaluation of the resulting
prototype. The application integrates Sarvam's existing speech recognition,
translation, and speech synthesis models.

The work went beyond generating an interface: the repository includes real API
integration, independent speaker handling, reconnect and usage controls, a
controlled benchmark, and archived bilingual human reviews. The evaluation also
records mistakes and playback delays so that the next improvements follow the
evidence.

## What participants can do

- Join a shared browser room with up to five participants and choose what to hear.
- Speak English, Hindi, Tamil, or Telugu without changing their hearing preference.
- Use up to two independent microphone streams concurrently.
- Follow live transcripts and receive translated text and audio in their language.
- Mute explicitly or join as a listener without opening a speech-recognition stream.

Incoming translations play in order rather than cutting off a previous clip.
This preserves complete clips but can add waiting during overlapping speech.
Headphones are recommended to prevent playback from feeding back into the mic.

## Evaluation snapshot

The controlled benchmark ran on 12 October 2026; the consolidated report and
completed human review are dated 13 October 2026.

| Question | Observed result | Scope |
| --- | --- | --- |
| Was meaning preserved across languages? | 62 of 72 cross-language outputs (86.1%) scored at least 4/5 | Bilingual human text/audio review; 30 synthetic source clips; one alternating repetition |
| Did translated outputs reach listeners? | 540 of 540 stable-listener outputs delivered across 180 input runs | Controlled localhost/Docker run using real Sarvam APIs |
| How long did complete audio receipt take? | p95 4.49s alternating / 4.79s overlapping | Clip-end to last audio receipt; capture, WAN, and playback excluded |
| Did clips play completely? | 24 of 24 cached clips started and ended once | Browser replay across desktop and mobile-sized viewports |
| Was repeated work avoided? | 25% fewer logical translation/TTS invocations | 540 shared pipelines for 720 recipient deliveries versus per-recipient processing |

These results describe the measured samples, not a production reliability or
universal translation-accuracy claim. Across all 90 human-reviewed outputs,
77 scored at least 4/5; 13 fell below that threshold. Some mixed-language and
Telugu inputs produced meaning errors. Overlap playback queueing remains an
important experience limitation. Human satisfaction and real microphone-to-heard
latency have not been quantified.

See the [consolidated report](docs/TESTING-REPORT.md),
[review summary](loadtest/results/reviewed_20261013/human_review_summary.json), and
[90 reviewed observations](loadtest/results/reviewed_20261013/reviewed_observations.csv).
The [original controlled benchmark](loadtest/results/controlled_20261012/REPORT.md)
is retained as the historical quantitative study, before human review was added.

## Demo access

The Render link is an interface preview with live speech processing intentionally
disabled. A private two-device live demo was verified previously; there is no
always-on public live interpreter. Use the local setup below or the
[private laptop demo](DEPLOYMENT.md#no-hosting-cost-temporary-laptop-demo)
for actual speech translation.

Live processing requires a Sarvam key and a private invite. Usage allowances
persist in SQLite; listen-only participants use no upstream STT. Short reconnects
preserve participant identity and the STT session within a default 10-second grace
period. Audio captured during a disconnect is discarded.

## How it works

```text
Browser microphone -> independent Sarvam Saaras STT stream
                   -> committed transcript
                   -> group other listeners by their hearing language
                   -> Sarvam Mayura translation -> Sarvam Bulbul TTS
                   -> translated text/audio over WebSockets -> queued playback
```

The backend uses Python and FastAPI; the browser uses JavaScript and an
AudioWorklet to send 16kHz PCM16 audio. Each speaker has an independent pipeline.
Translation and synthesis run once per distinct target language and the resulting
audio is reused for matching listeners. Language groups are processed concurrently.
Model inference runs through Sarvam's cloud APIs. Docker packages the backend
and browser application.

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r backend/requirements.txt
```

Create `.env` in the project root:

```
SARVAM_API_KEY=your_key_here
LIVE_ENABLED=true
DEMO_ACCESS_TOKEN=use_a_random_secret_of_at_least_32_characters
```

## Running

```bash
.venv\Scripts\python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in a real browser (mic access needs a real
browser permission grant, not an embedded/sandboxed preview). `localhost`
counts as a secure context, so plain HTTP is fine here.
Enter your private demo invite before joining. See `.env.example` for usage
caps and configuration; leave `LIVE_ENABLED=false` for a zero-credit preview.

### Running with HTTPS (needed for a second device)

Browsers only grant mic access on a secure context -- `https://`, or exactly
`localhost`/`127.0.0.1`. A phone opening the laptop's `http://<LAN-IP>:8000`
is neither, so it'll get an explicit "mic access requires HTTPS" error. Fix
it with a self-signed cert scoped to your LAN IP (stays local, nothing
exposed to the internet):

```bash
mkdir certs
cd certs
MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes \
  -subj "/CN=Live Speech Interpreter" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:<your-laptop-LAN-IP>"
cd ..
```

(`MSYS_NO_PATHCONV=1` is only needed on Git Bash for Windows, which
otherwise mangles the leading `/CN=...`.)

Then run the server with TLS:

```bash
.venv\Scripts\python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem
```

Both devices now use `https://` (see demo steps below). The cert is
self-signed, so **every browser will show a "connection is not private"
warning the first time** -- click **Advanced -> Proceed** (wording varies
by browser). This is expected and safe here: it's still only reachable on
your local network, this just isn't a certificate a public CA has vouched
for. `certs/` is gitignored -- regenerate it if your laptop's LAN IP
changes (different network, DHCP renewal, etc).

## Demo: two devices

1. Start the server with HTTPS (above). Note the laptop's LAN IP (Windows:
   `ipconfig`, look for the Wi-Fi adapter's IPv4 address) if you haven't
   already, for the cert step.
2. **On the laptop**: open `https://localhost:8000`, click through the
   self-signed cert warning.
3. **On the second device** (phone), connected to the **same Wi-Fi
   network**: open `https://<laptop-LAN-IP>:8000`, click through the same
   warning.
4. On each device, enter the **same room code**, pick the language *that
   device's user wants to hear* (not what they'll speak -- speak however
   you like), click **Join & Start**, and grant mic access. Up to 5
   devices can join one room.
5. **Wear headphones on every device.** Without them, translated audio
   played on a device gets picked up by that device's own mic and garbles
   its transcription -- this is a hard requirement, not a nice-to-have.
6. For the recruiter-facing view: mirror/project the **laptop's** screen.
   Its participant grid shows everyone's live transcript, and the "incoming"
   panel shows whatever's being translated for that specific device's
   language, even though only one physical device is visible.

If a device can't reach `https://<laptop-LAN-IP>:8000` at all (not even
the cert warning), that's a network issue, not a TLS one -- common on
networks that isolate devices from each other (e.g. some guest Wi-Fi).
Check that Windows Firewall allows inbound TCP on port 8000, or use a
tunnel / deployed backend instead if the two devices genuinely can't reach
each other on the LAN. Don't use a public tunnel (ngrok, localtunnel, SSH
reverse tunnels, etc.) without deciding that deliberately -- it exposes
this server to the open internet. Keep invite access, origin restrictions,
session limits, and the persistent usage ledger configured before exposing it.

## Deploying beyond localhost

Hosting requirements beyond a LAN demo:

- **HTTPS is required.** Browsers only grant microphone access
  (`getUserMedia`) on a secure context -- `https://` or exactly
  `localhost`/`127.0.0.1`. Serving over plain `http://` from any other host
  or IP will make the frontend refuse to request the mic at all (the app
  detects this and shows an explicit error rather than failing silently).
  The self-signed cert above is enough for a LAN demo (with a one-time
  click-through warning); for a real public deployment, put the app behind
  a reverse proxy or platform that terminates TLS with a CA-issued cert
  (nginx + certbot, Caddy, or a PaaS like Render/Fly/Railway that provides
  HTTPS automatically) instead of shipping a self-signed one.
- **Run a single worker process.** Room/session state (`SESSIONS` in
  `backend/app.py`) lives in memory in one process. Running multiple
  worker processes (e.g. `uvicorn ... --workers 4`, or a process manager
  that forks) would split participants across workers that can't see each
  other's rooms. Scale vertically (more CPU) or move session state to a
  shared store (Redis, etc.) before scaling horizontally -- not done here.
- `GET /health` returns `{"status": "ok", "active_rooms": N}` for
  platforms that expect a health-check endpoint.

## Measurement boundaries and remaining work

- **Reconnects:** a short network drop resumes the same participant and STT
  connection using a private resume token. Audio captured during the drop is
  discarded rather than replayed. Restarted servers and expired sessions cannot
  recover upstream STT state.
- **Structured tests:** raw JSON and per-pair summary CSV are now checkpointed
  durably. The full campaign completed 92 utterances / 368 target outputs. Synthetic test
  audio measures this pipeline, not human conversational accuracy.
- **Timing:** `total_ms` remains the server pipeline timing (speech-start VAD
  event to transcript commit, translation, synthesis). The harness separately
  records send-start to listener audio receipt as `end_to_end_ms`. The browser
  reports receive-to-playback and approximate capture-to-playback timing with
  clock RTT. Approximate capture uses the first above-threshold PCM chunk, not
  acoustic speech-end; these metrics must not be conflated.
- **Feedback:** browser AEC, noise suppression, and automatic gain control are
  requested. The optional playback guard sends silence while translated audio
  plays; it is off by default for two-speaker capture. Headphones remain recommended; robust
  full-duplex echo suppression needs separate device testing.
- **Deployment:** private invite access is implemented. Rooms still live in one
  process; run one worker/instance. Distributed routing, broader real-speech evaluation, and a full 30-minute
  endurance run remain future work. The completed bilingual human review is
  documented in [the consolidated report](docs/TESTING-REPORT.md).
- **Stretch goals not built**: real telephony bridge, voice cloning,
  document AI integration -- all explicitly out of scope for the core demo.

## Project layout

```
backend/app.py          FastAPI app: WebSocket room/pipeline logic
backend/requirements.txt
docs/TESTING-REPORT.md  Consolidated measurements and bilingual human review
loadtest/results/reviewed_20261013/  Archived human scores and derived summaries
frontend/index.html     Participant grid UI, room join, mic capture wiring
frontend/audio-processor.js   AudioWorklet: resamples mic input to 16kHz PCM16
chat.py                 Minimal Sarvam chat completion smoke test
phase0_realtime_stt.py  Standalone realtime STT verification script
loadtest/generate_fixtures.py  One-time: synthesizes the 4 test clips (run once)
loadtest/fixtures/*.pcm  Pre-recorded speech clips used as simulated mic input
loadtest/run_load_test.py  Runs N full 5-person sessions back to back
loadtest/test_set.py    92 scripted sentences (20 per language + 12 code-switched)
loadtest/generate_test_fixtures.py  One-time: synthesizes audio for test_set.py
loadtest/test_fixtures/*.pcm  Synthesized audio for the structured test set
loadtest/run_structured_test.py  Runs every test_set.py utterance, one room each
loadtest/results/*.json  Raw per-session/per-utterance results from actual runs
logs/metrics.jsonl      Per-utterance latency log (gitignored, created at runtime)
```

## Running the load test

```bash
.venv\Scripts\python loadtest\generate_fixtures.py   # once, needs SARVAM_API_KEY
.venv\Scripts\python loadtest\run_load_test.py       # dry run; --live opens one paid session
```

Needs the server already running (any of the commands under "Running"
above). Writes `loadtest/results/load_test_<timestamp>.json` with raw
per-participant event timing for every session.

## Structured test set (per-language-pair latency numbers)

`loadtest/test_set.py` has 20 short scripted sentences per language
(Hindi, English, Tamil, Telugu) plus 12 intra-sentence code-switched
Tamil-English (Tanglish-style) sentences -- 92 total. The non-English
sentences are model-generated, not verified by a native speaker.

```bash
.venv\Scripts\python loadtest\generate_test_fixtures.py   # checks existing fixtures; no API calls
.venv\Scripts\python loadtest\run_structured_test.py       # zero-credit cost preview
.venv\Scripts\python loadtest\run_pilot.py --live          # bounded 5-utterance campaign
```

Each utterance runs in its own room: one speaker plus four listeners
(one per hearing language), reusing the same fan-out the app already
does in conference mode -- one STT pass produces four (source, target)
data points per utterance, giving 20 samples for each of the 16
combinations (12 cross-language + 4 same-language). Records both the
server-measured stt_ms/translate_ms/tts_ms/total_ms and a harness-
measured end_to_end_ms (send-start to audio-received on the listener's
own socket) -- the true wall-clock number the earlier phases flagged as
missing. Writes `results/structured_test_<timestamp>.json` (raw, every
data point, nulls where something genuinely didn't arrive) and a
`_summary.csv` with p50/p95 per pair and overall.

**Historical status before the portfolio hardening pass:** First attempt got through 29 of
92 utterances (196 real data points, since discarded when the retry
started clean) before hitting `"Credits exhausted. Visit the API
Dashboard to review and manage your subscription."` from Sarvam. Along
the way, found and fixed a real bug in the harness itself: it used
`asyncio.gather()` without cancelling sibling tasks on failure, so one
utterance's connection dropping left its other four tasks running in
the background for their own 30-second timeout while the loop moved on
-- across enough utterances those piled up. Also replaced an
`asyncio.Barrier` (made every listener wait for the slowest of 5
connections, up to ~3.8s per Priority 1's numbers, before the speaker
even started) with a fixed 1-second delay, since a listener's own idle
STT session sitting unused for longer than necessary seems related to
why connections were dropping. This was the historical blocker; the portfolio
pass has now completed the full campaign using listen-only participants,
readiness negotiation, explicit trailing silence, and durable checkpoints.
