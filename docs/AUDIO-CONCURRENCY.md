# Microphone control and two-speaker overlap

## Explicit Unlimited Testing

With spending approval, `scripts/local_demo.py start --live --unlimited`
removes cumulative STT, translation and TTS allowances for the local demo.
Usage remains recorded in the persistent ledger. Sarvam balance and provider
limits still apply; unlimited-credit demo sessions now last 30 minutes. Keep the invite
private and stop the demo when testing ends. Omitting `--unlimited` restores
limited mode on the next startup; the Render preview remains disabled.

## Session Timeout Fix

Saved metrics for room `test2` showed both upstream streams connecting at
21:23:02 UTC and ending with `TimeoutError` at 21:24:02 UTC: a one-minute
application timer, not a demonstrated provider failure. Unlimited credit caps
had not removed `SESSION_SECONDS=60`. The enclosing `asyncio.timeout` raced the
inner timed wait and obscured the expiry as a generic session failure.

Bounded timeouts now report `Demo session time limit reached`. On 2026-10-12
the user explicitly chose a 30-minute duration rather than indefinite billing.
Local unlimited-credit mode therefore defaults to 1800 seconds. An explicit
`--session-seconds` can configure a different positive duration. The backend
also supports an explicitly configured unlimited duration only with an
unlimited STT allowance, reserving successive minute blocks in that mode.
This mode is not enabled on the deployed demo.

Regression tests cover a bounded timeout message, an unlimited listener,
independent concurrent streams and ongoing accounting. The paid soak command
`scripts/test_live_overlap.py --live --soak-seconds 210` keeps two real provider
streams and a listener open past the reported three-minute failure window,
then repeats both fixture utterances to verify translation still works.

The deployed Docker soak passed: listener audio from B/C arrived at 5.453s /
5.797s initially and again at 214.859s / 215.094s (sequence 2). Total run:
215.203s, no error events. All three test sockets closed cleanly and health
reported zero active rooms afterward. Evidence is ignored runtime data in
`logs/local-demo/live-soak-result.json`. This is a real-provider loopback test,
not a two-physical-laptop or full-30-minute endurance test. Host and Linux Docker
backend tests (28) and desktop/mobile browser regressions also passed.

## Scope

The user reported that a second microphone became silent even when playback
muting was unchecked. The original code had no manual mute protocol, no visible
capture state, and interrupted any playing translation on new audio arrival.
Those defects are addressed. The exact device-dependent cause of the reported
silence is not established without a two-laptop reproduction; browser audio
processing, listen-only selection, provider failures and quota exhaustion can
also affect what is heard.

## Research

Zoom documents echo cancellation and noise suppression enabled by default:
https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0064198

Meet describes cancelling non-speech noise, preserving voices, and adapting
device/cloud processing; this is not a specification of its private mixer:
https://support.google.com/meet/answer/9919960

WebRTC's open-source Audio Processing Module supplies AEC, noise suppression
and automatic gain control:
https://webrtc.googlesource.com/src/+/refs/heads/main/modules/audio_processing/g3doc/audio_processing_module.md

Our implementation uses browser-provided AEC/NS/AGC and separate PCM streams.
It does not reproduce proprietary Zoom/Meet audio engines, perform neural
source separation, or introduce a paid conferencing service. Headphones and
separate devices/rooms remain the dependable small-scale setup. Two people
captured in the same microphone cannot be reliably separated by this change.

## Behaviour

- A microphone button beside session controls explicitly mutes/unmutes.
  Accessible action labels, pressed state, a tooltip, status and input meter
  distinguish manual mute, listen-only mode, native device unavailability and
  the optional playback pause. The controls reuse existing UI tokens and local
  Lucide icons; there is no Storybook/Figma system in this repository.
- Manual mute immediately disables the local track and sends silence. The
  server independently zeros muted PCM before sending it to STT. Previously
  captured utterances may still finish translation after mute.
- The room admits at most two unmuted speaker participants. A third starts
  muted and may explicitly request unmute after another mutes or leaves. A
  rejected request leaves capture muted and shows why. No automatic promotion.
- Listen-only participants cannot unmute; they must stop and rejoin as a speaker.
- Each of the two speakers keeps its own upstream STT connection, timestamps,
  final transcripts and ordered translation worker. Their input is never mixed
  before STT, and translation remains concurrent across speakers.
- The playback-mute checkbox is off by default. When enabled, it disables the
  local audio track and sends silence while a translated clip is active; it does
  not change manual mute state. The crossed-out mic and subtle pulse identify
  the automatic pause; reduced-motion preferences disable the pulse.
  End/error/rejected playback clears the pause. A manually muted mic stays muted.
- Translated clips play in arrival order without interrupting earlier clips.
  Eight clips may wait; excess audio is rejected with a visible message while
  captions remain. Per-party translation text is preserved. This favours
  intelligibility over simultaneous TTS mixing; queueing increases latency.
- Muting does not stop billing or refund the full reserved speaker session.
  Disconnected participants retain their mic slot during the reconnect grace.

## Verification

Offline backend tests cover two-slot contention, listener denial, server PCM
silencing, unmute recovery and two independent speaker translations delivered
to a common listener. Playwright uses generated fake microphone audio and
mocked WebSockets to verify desktop/mobile controls, silent/live PCM, capture
during playback with the guard off, guard recovery and queued speaker clips.
No Sarvam credits are used by these tests.

On 2026-10-10 the user approved INR 5, expandable to INR 20, for testing. A real
provider overlap smoke test passed using existing fixtures `en_0` and `en_2`,
sent concurrently through the running Docker backend on local loopback. Both
final transcripts, Hindi translations and audio messages reached a common
listener and the opposite speaker, with no provider errors. Completion was
6.203 seconds. This verifies independent concurrent provider streams, not two
physical microphones or public-tunnel authentication.

Incremental reservations: 120 STT seconds, 66 translation characters and 52
TTS characters. At the project's documented rates the conservative estimate
is INR 1.288 excluding GST, not a Sarvam billing receipt. No new fixture TTS or
automatic retries were used. Evidence is ignored local runtime data in
`logs/local-demo/live-overlap-result.json`; explicit paid reruns use
`scripts/test_live_overlap.py --live`. Two microphones multiply STT consumption.
Keep the existing persistent ledger; deployments do not reset allowances.

The reported failed join showed a network error loading the AudioWorklet and a
broken external SVG icon. Both assets were available through local Docker;
the old quick-tunnel hostname subsequently failed DNS resolution. The HTML
response now embeds the existing Lucide SVGs and worklet source, loading the
worklet from a local Blob URL, so these critical assets need no secondary
network requests. HTML uses `Cache-Control: no-store`. Browser regression tests
deliberately abort all `/static` requests and still join/capture successfully.

After approval, test on two laptops with headphones: leave listen-only and
playback muting unchecked, speak short overlapping phrases, confirm both
transcripts and both translated outputs, mute/unmute each mic, then repeat with
playback muting enabled to confirm automatic recovery. This validates real
device behaviour that fake microphone tests cannot establish.
