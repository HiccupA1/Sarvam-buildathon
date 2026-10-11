# Deployment and budget plan

The README contains the original phase plan. This file describes the portfolio
release, the remaining evidence, and its spending boundaries.

## Budget

The available balance is INR 500, excluding GST. Sarvam's published prices,
checked 2026-10-09, are INR 30/hour of STT, INR 20/10,000 input translation
characters, and INR 30/10,000 TTS characters:
https://docs.sarvam.ai/api/getting-started/pricing

1. Offline verification and fixture reuse: INR 0.
2. Five-utterance live pilot: about INR 5-15 expected, depending on output
   lengths. Its separate durable usage ledger reserves at most 225 STT seconds,
   4,000 translation characters, and 8,000 TTS characters: INR 33.875 at the
   published rates, before GST. Failed calls retain reservations conservatively.
3. Expand the checkpointed benchmark after examining pilot results. Preview the
   selected clips and estimated costs first. Use a distinct output filename for
   a new campaign; reuse that filename to resume. No automatic retries.
   `run_pilot.py --full --live` expands the successful pilot to all 92 clips,
   preserving completed samples and using a separate allowance of INR 294.50
   before GST. It spaces utterances at least 10 seconds apart to reduce bursts.
   Completed on 2026-10-09: all 92 utterances / 368 final outputs. Two failed
   attempts needed controlled retries. Both campaign ledgers total INR 112.111
   in conservative reservations before GST; actual provider billing was not queried.
4. Portfolio demos: default lifetime allowance INR 115 before GST (1,800 STT
   seconds, 20,000 translation characters, 20,000 TTS characters). Reserve the
   remaining balance for verification and supervised demos.

The ledger is a local application allowance, not a live Sarvam balance query.
Account usage by other scripts or apps, billing/model changes, and GST are not
included. A speaker reserves a complete session before opening STT, even if
they stop early. Listeners reserve no STT. Connection shutdown can add a small
tail beyond the configured session limit. Keep a provider-side balance margin.
Do not delete the usage database or redeploy it onto ephemeral storage during
a live campaign: that resets application allowances.

## Recommended first release

Use one Render Docker web service with HTTPS and WebSockets. One instance and
one worker match the current room ownership model and keep a small demo simple.
The free tier sleeps after inactivity and has ephemeral storage; it is suitable
for a disabled preview, not for a persistent credit-limited public live demo.
Choose a paid instance with a persistent disk for live access; inspect the
current hosting price before purchasing. Hosting is separate from Sarvam credit.

References:
https://render.com/docs/docker
https://render.com/docs/websocket
https://render.com/docs/free
https://render.com/docs/disks

### Free disabled preview

Deployed on 2026-10-10:
https://sarvam-interpreter-preview.onrender.com/

Render reported a successful Docker deployment of commit `d30805f`. Its
health-check logs showed HTTP 200, and the public HTTPS frontend displayed
`live demo offline` with Join disabled. No Sarvam key was supplied. This is
hosting verification, not an end-to-end live speech test.

The initial Blueprint used the public repository URL without granting Render
repository access. Render warns that automatic code deployments are unavailable
for an unconfigured public repository. Use Manual Deploy for new application
commits; connecting the Git provider later requires reviewing and approving
repository permissions. Do not assume CI passing automatically updates this
service. Blueprint configuration synchronization is separate from code deploys.

The repository includes `render.yaml` for a free Docker preview in Singapore.
In Render, create a new Blueprint from this repository and its `main` branch.
Review that the proposed service uses the free plan before creating it. It
uses `/health` and deploys updates after CI passes. No Sarvam key or invite is
required: live access is disabled and all paid API allowances are zero.

This preview has no persistent disk and must not be converted to live access
by only changing `LIVE_ENABLED`. Before enabling live sessions, approve the
hosting cost, configure durable storage and secrets, and replace the zero
allowances with reviewed limits. Update the Blueprint too, or a later sync can
restore its disabled-preview settings. Never commit secrets to `render.yaml`.

### No-hosting-cost temporary laptop demo

Use the local Docker app and Cloudflare Quick Tunnel for supervised testing.
Your laptop, Docker, and internet connection must stay running; visitors need
only a browser and headphones. This is not a permanent hosting deployment.
Cloudflare relays browser traffic (including the demo invite, audio, and text).
The Sarvam key stays in the local backend configuration and is not provided to
Cloudflare, Render, GitHub, or visitors. Speech processing still calls Sarvam.

```powershell
# Run from the repository root. The local .env must contain SARVAM_API_KEY.
docker build -t sarvam-interpreter:portfolio .
docker pull cloudflare/cloudflared:latest
.venv\Scripts\python.exe scripts\local_demo.py start --live
.venv\Scripts\python.exe scripts\local_demo.py status
# Optional public listener-only check; this sends the demo invite via Cloudflare.
.venv\Scripts\python.exe scripts\local_demo.py verify
# Always stop after testing:
.venv\Scripts\python.exe scripts\local_demo.py stop
```

Startup prints the temporary URL, but never the Sarvam key. The private invite
is in `logs/local-demo/PRIVATE-INVITE.md`; this directory is ignored by Git.
Do not publish the invite or include it in a URL. Each start creates a new link
and invite, but reuses the `sarvam-local-demo-budget` Docker volume. Do not delete
that volume to reset allowances. Stop removes only labeled demo containers and
generated local secret files; it retains the budget. Stopping Docker alone does
not remove those files, so use the stop command when possible.

The initial allowance is 240 reserved STT seconds, 1,000 translation input
characters, and 1,000 TTS characters, about INR 7 in reservations at the rates
above, before GST. This is not an actual billing cap. Each speaker join reserves
60 seconds even if stopped early. Listeners use no STT. No automatic paid test
is performed by startup. Limits are lifetime allowances across starts, not
per-demo allocations. The demo stays reachable until explicitly stopped.

For explicitly approved uncapped-credit testing, use
`scripts/local_demo.py start --live --unlimited --session-seconds 1800`.
This removes cumulative credit allowances but keeps a 30-minute session timer
(also the default duration with `--unlimited`). Every speaker join conservatively
reserves the full 30 minutes in the usage ledger; this is not actual provider
billing. Share the invite only with supervised testers and stop afterward.
The earlier one-minute timer is still used in limited mode. Expiry now shows
`Demo session time limit reached`, not a generic provider error.

For two-laptop testing:

1. Open the temporary URL on both devices (not the disabled Render preview).
2. Enter the same unique room code and private invite on both.
3. Set each device's preferred hearing language; keep headphones on.
4. On laptop B, check Listen only and join first. On laptop A, leave it unchecked,
   join, and allow microphone access. A speaker alone still consumes STT budget.
5. Say one short sentence on A and confirm B receives translated text and audio.
6. Stop both within 60 seconds. Swap roles and repeat with a different language.
7. Check status for reservations. Record any errors and observed latency; do not
   repeatedly rejoin after an error because each speaker join reserves a session.
8. Stop the demo after testing. Do not share sensitive conversations in a demo.

Quick Tunnels have changing hostnames and no uptime guarantee:
https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/

### Persistent Render live release

1. Push the reviewed source to your GitHub repository after offline CI passes.
   The Docker context excludes credentials, private certificates, logs, fixtures,
   and test results. Docker contains only the runtime app and frontend.
2. Create a Docker web service from the repository. Use `/health` as the health
   check path. Keep one instance, one worker, and autoscaling off.
3. Mount a persistent disk at `/app/logs`. Set `USAGE_DB_PATH` to
   `/app/logs/usage.sqlite3`. The non-root container user must be able to write
   this directory; verify disk permissions in a disabled deployment first.
   Run `python -c "from backend.app import BUDGET; BUDGET.reserve('stt_seconds', 0)"`
   in the platform shell to confirm the ledger can be created without paid calls.
4. Configure `SARVAM_API_KEY` in platform secrets. Generate a random demo invite
   of at least 32 characters and set `DEMO_ACCESS_TOKEN`. Do not embed it in the
   source, a query URL, or your public resume.
5. Initially set `LIVE_ENABLED=false`; visit `/health`, `/config`, and the UI.
   Set `ALLOWED_ORIGINS=https://YOUR-SERVICE.onrender.com` (no trailing slash).
6. Set `LIVE_ENABLED=true` for supervised tests. Join with the invite; use one
   speaker and listen-only participants. Test with headphones on two devices.
   Verify recording permission, correct language delivery, reconnect, session
   expiry, and playback metrics before sharing the live URL.
7. Put the URL, repository, and a short recorded demonstration on your resume.
   Share temporary invites privately for interviews; rotate the invite after.
   Set `LIVE_ENABLED=false` whenever the live demo is not needed.

An invite protects billable access; this is not a multi-user identity system.
Anyone with that invite can join other rooms whose codes they know. Use unique
room codes per interview and keep invitations private. HTTPS protects transport.
The application logs timings and character counts, not microphone audio or
transcript content. Benchmark result files intentionally contain scripted text.

## Verification commands

For a zero-credit local Docker preview:

```powershell
docker build -t sarvam-interpreter:portfolio .
docker run -d --name sarvam-portfolio-preview -p 127.0.0.1:8778:8000 -e LIVE_ENABLED=false -v sarvam-portfolio-verification:/app/logs sarvam-interpreter:portfolio
```

Visit `http://127.0.0.1:8778`. This is intentionally not a live demo. The named
volume persists the budget ledger across container restarts; never reset the
ledger for a live deployment. Other people need only a browser to use a hosted
deployment, not Docker. Running the container on their laptop requires Docker
or a compatible container runtime.

The optional Playwright browser checks can target this container with
`PREVIEW_URL=http://127.0.0.1:8778`; they mock WebSockets and make no Sarvam calls.
They require Playwright and Microsoft Edge installed on the test machine.

Verified on 2026-10-10: the Linux image builds, all 18 offline backend tests
pass inside Docker, and desktop/mobile browser checks pass against the local
container. Non-root ledger writes, persistence across restart, disabled live
mode, and exclusion of local credentials were checked without paid API calls.

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe loadtest\run_structured_test.py
.venv\Scripts\python.exe loadtest\run_pilot.py --live
.venv\Scripts\python.exe loadtest\run_pilot.py --full
.venv\Scripts\python.exe loadtest\report_results.py loadtest/results/structured_test_campaign.json
```

The pilot creates a short-lived local server and shuts it down after completion
or failure. It never prints the generated invitation. Its raw results and summary
CSV live in `loadtest/results/structured_test_pilot*`. It resumes completed samples.

For a larger campaign against an already running, configured server:

```powershell
# DEMO_ACCESS_TOKEN must match the server's secret invite.
.venv\Scripts\python.exe loadtest\run_structured_test.py --per-category 4 --max-utterances 20 --max-input-seconds 180 --max-source-chars 4000 --output loadtest/results/structured_test_expanded.json
# Review the dry-run estimate, then repeat with --live.
```

The full set uses `--per-category 20 --max-utterances 92`; choose input limits
only after reviewing the fixture totals. Each final transcript can fan out to
four targets; fragments can increase calls. The server ledger remains the spend
guard. The old concurrency benchmark remains a historical experiment, not the
default verification path.

## Evidence still required

- Native speakers must review Hindi, Tamil, Telugu, and mixed-language sentences
  and translations. Synthetic voice clips cannot establish human conversation
  accuracy. Preserve expected text, returned transcript, and translated text;
  record reviewer, correction, and pass/fail before making accuracy claims.
- One sample per language pair is a smoke check. Even when CSV reports p95, it
  is not statistically useful at that sample size. Expand samples before quoting
  percentile performance on a resume.
- Browser capture-to-playback timing uses approximate clock synchronization and
  the first above-threshold PCM chunk, not laboratory acoustic measurements.
  Clock RTT is logged alongside timing; missing capture timestamps stay missing.
  The scripted harness measures first translated audio receipt, which can be
  an initial fragment if VAD splits a longer clip. Review the text before making
  full-sentence translation quality claims.
- Browser AEC and optional playback-time microphone muting reduce feedback. The
  playback guard is off by default; enabling it makes interaction half-duplex. Full-duplex echo suppression needs
  device testing and a proven audio engine; headphones remain recommended.
- Redis/distributed room routing is not part of this single-instance release.
  Adding a shared dictionary alone is insufficient: audio delivery needs a
  cross-worker event bus, atomic capacity leases, participant ownership,
  expiry/heartbeat, and reconnect affinity. Verify these with multi-process tests
  before advertising horizontal scaling.
- Telephony, voice cloning, and document AI are optional original stretch goals
  and require separate integrations and budget; they are not portfolio blockers.
- `chat.py` and `phase0_realtime_stt.py` remain standalone exploratory scripts.
  They are intentionally excluded from the runtime container and offline CI.

## Resume wording

Built a multilingual speech interpreter with FastAPI, WebSockets, and Sarvam AI,
supporting five-person rooms and four listening languages; reduced redundant
API calls through per-language fan-out and listen-only sessions, and added
checkpointed benchmarks, reconnect recovery, and persistent usage limits.

Add "deployed over HTTPS" only after deployment and two-device verification.
Add measured latency or accuracy only with the matching sample size, measurement
boundary, and a completed evaluation. Do not claim native-speaker validation,
horizontal scaling, or full-duplex echo cancellation until those are verified.
