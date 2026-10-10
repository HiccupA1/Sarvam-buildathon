# Structured benchmark

Completed utterances: 92/92 recorded; full fixture set: 92.

Synthetic PCM input, one speaker and four listen-only targets, real-time paced audio.
End-to-end here means speaker send-start to the first translated audio receipt; it excludes playback.
VAD can split a clip into multiple transcripts; first-audio timing does not prove full-sentence delivery.
Each category labels the scripted input, not the language detected by STT.
Native-speaker quality review has not been performed.

| Source | Target | Samples | Missing | Receive p50 (ms) | Receive p95 (ms) |
| --- | --- | ---: | ---: | ---: | ---: |
| en | en-IN | 20 | 0 | 5712 | 6372 |
| en | hi-IN | 20 | 0 | 6361 | 7277 |
| en | ta-IN | 20 | 0 | 6707 | 7471 |
| en | te-IN | 20 | 0 | 6567 | 7511 |
| hi | en-IN | 20 | 0 | 6588 | 7876 |
| hi | hi-IN | 20 | 0 | 7285 | 8354 |
| hi | ta-IN | 20 | 0 | 7300 | 8724 |
| hi | te-IN | 20 | 0 | 7448 | 8711 |
| mix | en-IN | 12 | 0 | 7043 | 8332 |
| mix | hi-IN | 12 | 0 | 8042 | 9265 |
| mix | ta-IN | 12 | 0 | 7888 | 9228 |
| mix | te-IN | 12 | 0 | 7830 | 9517 |
| ta | en-IN | 20 | 0 | 7007 | 8793 |
| ta | hi-IN | 20 | 0 | 7721 | 9742 |
| ta | ta-IN | 20 | 0 | 7802 | 10096 |
| ta | te-IN | 20 | 0 | 7813 | 9641 |
| te | en-IN | 20 | 0 | 7488 | 8919 |
| te | hi-IN | 20 | 0 | 8084 | 9921 |
| te | ta-IN | 20 | 0 | 8153 | 9976 |
| te | te-IN | 20 | 0 | 8232 | 10359 |
| OVERALL |  | 368 | 0 | 7233 | 9700 |

Small-sample percentiles are descriptive only. Stage timings overlap with input duration;
do not describe this as speech-end-to-playback latency or human translation accuracy.

Raw data: `structured_test_campaign.json`. Native review worksheet: `structured_test_campaign_review.csv`.

## Campaign context

Controlled retries: 2. Zero final missing results does not imply zero failed attempts.
The first 14 successful samples preceded an explicit 0.8-second VAD silence tail; later samples included it.
These are developmental measurements, not a controlled production SLA study.

Conservative usage estimate: INR 112.111 excluding GST.
Reservations cover entire bounded STT sessions, even when a clip ends earlier. Actual Sarvam billing was not queried.
