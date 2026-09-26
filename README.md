# Live Speech Interpreter

Real-time speech interpreter for a room of up to 5 people, live, in the
browser. Everyone picks a language they want to **hear** in -- that's it.
What you actually **speak** is completely separate: any of the four
(Hindi, English, Tamil, Telugu), switch mid-conversation, or mix languages
within a sentence. The model figures out what was said; everyone else gets
it translated into whatever they personally picked. Built on Sarvam AI's
realtime STT, translation, and TTS APIs.

## Architecture

```
mic (browser) --PCM16/16kHz--> FastAPI backend --> Sarvam realtime STT (language_code="auto", mode="codemix")
                                                          |
                                                   transcript.final (whatever was actually said)
                                                          v
                                    group other participants by THEIR hearing-language preference
                                                          v
                        Mayura translate, source_language_code="auto" (once per distinct language) -- concurrent
                                                          v
                                          Bulbul v3 TTS (once per distinct language)
                                                          v
                                 WebSocket --> that language's listeners --> playback
```

Each participant runs their own one-directional pipeline (mic -> their own
STT, always `language_code="auto"` so they're never boxed into one
language); audio flows in one direction per speaker, through the pipeline,
once per *distinct target language* among the other participants -- not
once per listener. With up to 5 people but only 4 supported languages, at
least two participants must share a hearing-language preference once the
room has 5 people, so that translate+TTS call is naturally reused for all
of them rather than repeated. The backend is the hub (not peer-to-peer, not
mesh) -- everyone connects to the same room on the backend, which fans out
translated text/audio to the right listeners.

**Speak/hear are fully decoupled.** A participant's selected language is
only ever used as their *listening* preference (which translate/TTS group
they belong to when someone else talks) and never constrains what they
speak. There's no same-language shortcut anymore either -- since the
speaker's actual language isn't known ahead of time (it can vary utterance
to utterance), every utterance gets a real `translate()` call per
target-language group, with `source_language_code="auto"` so Mayura
detects it from the text itself. Verified: a participant who set their
hearing preference to Telugu but spoke English had their English correctly
transcribed (not forced into Telugu), and a listener whose preference was
Tamil received a correct Tamil translation of it.

Transcript and VAD events (in the speaker's own language) are broadcast to
**everyone**, so any device can render a full participant grid. Translated
captions, latency, and audio are sent only to the specific listeners in that
language group -- broadcasting audio room-wide would mean everyone hears
every language's synthesized speech, not just their own.

See [phase0_realtime_stt.py](phase0_realtime_stt.py) and the docstring at
the top of [backend/app.py](backend/app.py) for what was verified against
Sarvam's docs and why specific design choices were made (e.g. why STT's own
`translate` mode isn't used -- it only ever outputs English).

## Setup

```bash
python -m venv .venv
.venv\Scripts\pip install -r backend/requirements.txt
```

Create `.env` in the project root:

```
SARVAM_API_KEY=your_key_here
```

## Running

```bash
.venv\Scripts\python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in a real browser (mic access needs a real
browser permission grant, not an embedded/sandboxed preview). `localhost`
counts as a secure context, so plain HTTP is fine here.

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
this server, and your Sarvam API key's usage, to the open internet with no
authentication in front of it.

## Deploying beyond localhost

Two hard constraints if you host this somewhere other than a LAN demo:

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

## What's implemented

- **Phase 0-1**: single-leg pipeline (mic -> STT -> translate -> TTS ->
  local playback), verified against live Sarvam docs.
- **Phase 2**: two-party room model, correct audio/text routing between
  participants.
- **Phase 3**: explicit, documented VAD commit policy; strictly ordered
  per-speaker translation/TTS (no race conditions between rapid
  utterances); barge-in policy (new audio interrupts currently-playing
  audio rather than queuing, to keep lag bounded).
- **Phase 4**: two-pane UI (built for a single shared screen), VAD pulse
  indicators, live latency meter with sparkline.
- **Phase 5**: per-utterance latency logged to `logs/metrics.jsonl`
  (`stt_ms` = speech-start to transcript-commit, `translate_ms`, `tts_ms`,
  `total_ms`).
- **Hardening pass**: WebSocket auto-reconnect with backoff if the
  connection drops unexpectedly (mic stays live, a fresh STT session
  starts on reconnect); secure-context check before requesting mic access
  (clear error instead of a silent failure over plain HTTP on a non-
  localhost host); friendly messages for mic-permission-denied/no-mic;
  audio blob URLs are revoked after use; best-effort cleanup on tab close
  so a closed tab doesn't leave a phantom participant in a room; visual
  connection-status indicator.
- **Rejoin fix**: leaving a room used to leave it permanently occupied.
  The handler waited for both the audio-relay loop *and* the STT event
  loop to finish before cleaning up, but the STT loop only ends when
  Sarvam's server closes its side -- not guaranteed to happen just
  because the client said it's done. Fixed to tear down on a bounded
  grace period instead of an unbounded wait (see backend/app.py).
- **Phase 6 (conference mode)**: up to `MAX_PARTICIPANTS = 5` people per
  room, each with their own selected language. A committed utterance is
  translated/synthesized once per distinct target language among the
  other participants (concurrently), not once per listener, and reused
  across everyone who picked that language. Verified: 5/5 capacity with
  the 6th join rejected while all 5 are still connected, correct fan-out
  to all 4 languages from one speaker, and the two same-language
  listeners sharing a single translate+TTS pass.

  **Latency caveat, measured not assumed**: across two 5-participant test
  runs, per-listener STT-commit latency (`stt_ms`) was ~3.1s in one run
  and ~17.5s in the other for the same utterance, both with 4 of 5
  connections idle. It's not yet clear whether that's a one-off
  cold-start effect (opening 5 realtime STT connections nearly
  simultaneously) or something that recurs -- a direct SDK test confirmed
  Sarvam does support 2+ concurrent realtime connections without error,
  so it isn't a hard rejection, but conference latency under real load
  with all 5 slots filled hasn't been characterized enough to promise a
  number. Worth watching before relying on a full room for a live demo.
- **Phase 7 (decoupled speak/hear languages)**: a participant's selected
  language now governs only what they *hear*. STT switched from a fixed
  `language_code` per participant to `language_code="auto"` +
  `mode="codemix"`, so anyone can speak any supported language, switch
  mid-conversation, or mix languages within an utterance. Translation
  switched to `source_language_code="auto"` for the same reason -- the
  speaker's language is no longer known ahead of time, so there's no
  same-language shortcut anymore; every utterance gets a real translate
  call per target-language group. Verified: a participant whose hearing
  preference was Telugu but who spoke English had it transcribed
  correctly as English (not forced into Telugu), and a Tamil-preference
  listener got a correct Tamil translation of it.

## Known limitations / not yet built

- **No reconnect handling for the client's own session.** A dropped socket
  removes that participant from the room; they'd need to refresh and
  rejoin (the room itself becomes rejoinable promptly -- see the rejoin
  fix above -- but no state is preserved for the rejoining device).
- **No structured test set has been run.** The instrumentation captures
  real per-utterance timing from day one, but a scripted multi-language
  test set with reported real numbers (per language pair) needs live mic
  input across actual conversations -- that hasn't been executed yet.
  Don't quote `logs/metrics.jsonl` numbers as representative until it has.
- **`total_ms` is a partial end-to-end number.** It covers STT-commit +
  translate + TTS-generation. It does **not** include mic-capture/network
  transit to the backend or the client-side delay between receiving audio
  and playback actually starting -- those need client-reported timestamps
  that aren't wired up.
- **No acoustic echo cancellation.** Headphones are required (see above)
  instead; AEC is an explicit stretch goal in the build plan, not core.
- **Stretch goals not built**: real telephony bridge, voice cloning,
  document AI integration -- all explicitly out of scope for the core demo.

## Project layout

```
backend/app.py          FastAPI app: WebSocket room/pipeline logic
backend/requirements.txt
frontend/index.html     Two-pane UI, room join, mic capture wiring
frontend/audio-processor.js   AudioWorklet: resamples mic input to 16kHz PCM16
chat.py                 Minimal Sarvam chat completion smoke test
phase0_realtime_stt.py  Standalone realtime STT verification script
logs/metrics.jsonl      Per-utterance latency log (gitignored, created at runtime)
```
