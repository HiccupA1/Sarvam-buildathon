# Recording the Sarvam interpreter demo

Record 60–90 seconds showing real speech, a translated caption, and the translated
voice. The simplest setup is two browser windows on one laptop: one speaker and
one listener. A second laptop is optional. The hosted Render preview has live
processing disabled and cannot demonstrate translation.

## Prepare the app

1. Use headphones. Keep the browser, recorder, and headset on the same output
   device so the recorder captures the translated voice.
2. Use the repository's Python environment and local `.env`. It must contain a
   valid `SARVAM_API_KEY` and a private `DEMO_ACCESS_TOKEN` of at least 32 characters.
   Keep both values out of recordings and shared files.
3. In PowerShell, from the repository root, run:

   ```powershell
   $env:LIVE_ENABLED = "true"
   $env:SESSION_SECONDS = "300"
   .venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1
   ```

   This creates a five-minute session window for recording. Actual speech calls
   use Sarvam credits and existing persistent usage allowances still apply. Each
   speaker join reserves the configured session duration, including on early
   departure; the reservation is not a provider billing receipt. Join once and
   stop promptly. An allowance error needs a deliberate budget review, not deletion
   of the usage database or repeated rejoins.
4. Open `http://localhost:8000` in two Chrome or Edge windows. Both windows must
   use exactly the same address and room code, for example `recording-demo`.
5. In window A, choose **Tamil**, leave **Listen only** unchecked, enter the
   private invite, join, and allow microphone access. Confirm **Microphone live**;
   use the microphone button if it is muted.
6. In window B, choose **English**, check **Listen only**, enter the same invite,
   and join. B now displays and plays English translations of A's speech.
7. Enter the invite before recording. Tile the windows for OBS, or keep B visible
   for a simpler single-window Game Bar recording. B also shows live source
   transcripts. Mute A in the application while narrating your introduction so
   narration does not become demonstration input; unmute A for the sample speech.

If the Python environment is not installed, follow [Setup](../README.md#setup).
For two physical devices, use the README's HTTPS instructions or the existing
[private laptop demo](../DEPLOYMENT.md#no-hosting-cost-temporary-laptop-demo).
Plain HTTP at a laptop's LAN IP will not provide browser microphone access.

## Set up recording

### Recommended for two windows: OBS Studio

Use the OBS Auto-Configuration Wizard for recording. Add a **Display Capture**
source to show both browser windows. In the audio mixer, select the correct
**Desktop Audio** output and **Mic/Aux** input. If you use explicit Audio Input or
Output Capture sources instead, avoid capturing the same device twice. A readable
1080p recording at 30 fps is sufficient for this application demonstration.

Make a ten-second sound check before the main take: speak once and let the
translated voice play. Watch both audio meters and play back the saved file to
confirm the recording contains your speech and the translation. Headphones keep
desktop playback from being picked up acoustically by the microphone.

### Built-in option for one window: Xbox Game Bar

Focus the listener's browser window. Press **Win+G**, open the capture settings,
and include system audio and your microphone. **Win+Alt+R** starts/stops recording;
**Win+Alt+M** toggles the recorder's microphone. Game Bar records a selected app,
so use OBS when showing a desktop layout containing two windows. The recorder's
microphone toggle and the interpreter's microphone mute are separate controls.

Official help: [OBS quick start](https://obsproject.com/kb/quick-start-guide/),
[OBS audio sources](https://obsproject.com/kb/audio-sources), and
[Microsoft Game Bar recording](https://support.microsoft.com/en-us/accessibility/windows/use-a-screen-reader-to-record-your-screen-with-xbox-game-bar).

## Suggested 90-second take

| Time | Show | Say or do |
| --- | --- | --- |
| 0–12s | App and two joined participants | “I built this interpreter so people can speak naturally while each listener hears their preferred language.” |
| 12–20s | A set to Tamil, B set to English | Explain that the selector controls hearing, not speaking. Keep A muted during narration. |
| 20–40s | A's source transcript and B's translated text/audio | Unmute A and say: “நாளைய கூட்டம் காலை பத்து மணிக்கு தொடங்கும்.” Wait for the complete English caption and voice. The intended meaning is “Tomorrow's meeting starts at ten in the morning.” |
| 40–65s | A still set to hear Tamil | Say in English: “Please send the report before Friday.” B hears English; the source transcript shows that A spoke English without changing the Tamil hearing preference. Wait for playback to finish. |
| 65–78s | Optional role reversal | For a Tamil output, stop B, clear **Listen only**, rejoin as a speaker, and mute A. Speak English through B and show the Tamil result on A. This adds a speaker reservation; omit it for the simplest take. |
| 78–90s | App or evaluation summary | Mute the app microphone before explaining that the idea received Sarvam recognition and credits, and that this solo prototype used AI-assisted coding and real APIs. |

Use a short sentence in a language you can speak and verify comfortably. The Tamil
sentence above is a proposed demonstration input, not a claim that this exact
sentence was part of the controlled benchmark. An alternative first pass is to
set B to Tamil and speak English through A, then keep B as a listener throughout.
The optional reversal is not needed to establish that the product works.

Keep the actual speech-to-caption-to-playback sequence continuous and leave the
waiting time visible. Trim setup and idle sections, but label any cuts or edits
that affect timing. This recording demonstrates behavior; the benchmark report
contains the performance measurements.

## After recording

1. Click **Stop** in both app windows, then stop the local server with **Ctrl+C**.
   Close that PowerShell window, or remove the temporary overrides there:

   ```powershell
   Remove-Item Env:LIVE_ENABLED
   Remove-Item Env:SESSION_SECONDS
   ```

2. Review the saved video for readable captions, audible translation, and absence
   of credentials, private invite values, notifications, or unrelated windows.
3. Upload it to Drive with **Anyone with the link — Viewer**, or use an unlisted
   video link. Open the shared link in a signed-out/private window to check access.
4. Add the verified link near the top of the README, labeled **Recorded live demo**.
   Do not use the disabled Render preview as the live demonstration link.

If recording fails, first check both participants have the same room code, the
speaker microphone is live, the listener has joined, and the output device matches
the recorder. Allowance and session-expiry errors are different from audio errors;
do not present an expired session as an upstream-model failure.
