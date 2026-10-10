# Microphone control and two-speaker overlap

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
- The playback-mute checkbox is off by default. When enabled, it sends silence
  only while a translated clip is active; it does not change manual mute state.
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

A proposed paid smoke test uses two 60-second speaker reservations (INR 1 at
INR 30/hour), about 240 total translation characters (INR 0.48), and about 600
TTS characters (INR 1.80). Expected reservations are around INR 3.28 before GST;
request approval for up to INR 5 as an estimate, not a provider billing guarantee.
No automatic retries. Two microphones multiply STT consumption. Keep the
existing persistent ledger and do not reset allowances as part of deployment.

After approval, test on two laptops with headphones: leave listen-only and
playback muting unchecked, speak short overlapping phrases, confirm both
transcripts and both translated outputs, mute/unmute each mic, then repeat with
playback muting enabled to confirm automatic recovery. This validates real
device behaviour that fake microphone tests cannot establish.
