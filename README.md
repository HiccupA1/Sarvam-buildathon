# Live Speech Interpreter

Real-time, two-party speech interpreter: two people speak different Indian
languages (Hindi, English, Tamil, Telugu -- any pair) and each hears the
other translated live, in the browser. Built on Sarvam AI's realtime STT,
translation, and TTS APIs.

## Architecture

```
mic (browser) --PCM16/16kHz--> FastAPI backend --> Sarvam realtime STT (per speaker's language)
                                                          |
                                                   transcript.final
                                                          v
                                              Mayura translate (source -> peer's language)
                                                          v
                                                   Bulbul v3 TTS
                                                          v
                                        WebSocket --> peer's browser --> playback
```

Each participant runs their own one-directional pipeline; audio flows in one
direction per speaker, through the same pipeline, twice. The backend is the
hub (not peer-to-peer) -- both parties connect to the same room on the
backend, which routes translated text/audio to the other participant.

Transcript, VAD, and translated-caption events are broadcast to **both**
participants (tagged by party), so a single shared screen can show both
parties' panes at once. Actual synthesized audio goes only to the intended
listener.

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
browser permission grant, not an embedded/sandboxed preview).

## Demo: two devices

1. Start the server on your laptop (command above). Note the laptop's LAN IP
   (Windows: `ipconfig`, look for the Wi-Fi adapter's IPv4 address).
2. **On the laptop**: open `http://localhost:8000`.
3. **On the second device** (phone), connected to the **same Wi-Fi
   network**: open `http://<laptop-LAN-IP>:8000`.
4. On both devices, enter the **same room code**, pick each device's own
   spoken language, click **Join & Start**, and grant mic access.
5. **Wear headphones on both devices.** Without them, translated audio
   played on a device gets picked up by that device's own mic and garbles
   its transcription -- this is a hard requirement, not a nice-to-have.
6. For the recruiter-facing view: mirror/project the **laptop's** screen.
   Its two-pane layout shows both parties' live transcripts, incoming
   translations, VAD activity, and the latency meter at once, even though
   only one physical device is visible.

If the phone can't reach `http://<laptop-LAN-IP>:8000` (common on networks
that isolate devices from each other, e.g. some guest Wi-Fi), that's a
network issue -- use a tunnel (ngrok / Cloudflare Tunnel) or a deployed
backend instead.

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

## Known limitations / not yet built

- **No reconnect handling.** A dropped socket removes that participant from
  the room; they'd need to refresh and rejoin. (Plan's Phase "session
  lifecycle" item.)
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
