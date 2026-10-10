"""Synthesizes audio for every sentence in test_set.py and saves it as raw
PCM16 mono 16kHz, so the structured test can stream them as simulated mic
input without needing a real speaker.

Run once: python loadtest/generate_test_fixtures.py
"""
import base64
import argparse
import os

from dotenv import load_dotenv
from sarvamai import SarvamAI

import test_set

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
FIXTURES_DIR = os.path.join(SCRIPT_DIR, "test_fixtures")
SAMPLE_RATE = 16000

load_dotenv(os.path.join(PROJECT_DIR, ".env"))

# Code-mixed text is mostly Tamil with English words embedded, so it is
# synthesized with the Tamil voice, same as a Tamil TTS engine would
# handle embedded English loanwords.
TTS_LANGUAGE_FOR = {"hi": "hi-IN", "en": "en-IN", "ta": "ta-IN", "te": "te-IN", "mix": "ta-IN"}


def synthesize(client, text, language_code):
    response = client.text_to_speech.convert(
        text=text,
        language_code=language_code,
        speaker="kavitha",
        model="bulbul:v3",
        output_audio_codec="linear16",
        speech_sample_rate=SAMPLE_RATE,
    )
    return base64.b64decode(response.audios[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    os.makedirs(FIXTURES_DIR, exist_ok=True)
    all_entries = test_set.ALL_STANDARD + test_set.ALL_CODEMIX
    missing = [(category, index, text) for category, index, text in all_entries
               if not os.path.exists(os.path.join(FIXTURES_DIR, f"{category}_{index}.pcm"))]
    print(f"{len(missing)} missing clips; {len(all_entries) - len(missing)} reused")
    if not args.live or not missing:
        return
    client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

    for category, index, text in missing:
        tts_language = TTS_LANGUAGE_FOR[category]
        out_path = os.path.join(FIXTURES_DIR, f"{category}_{index}.pcm")
        if os.path.exists(out_path):
            continue
        pcm_bytes = synthesize(client, text, tts_language)
        with open(out_path, "wb") as f:
            f.write(pcm_bytes)
        duration_s = len(pcm_bytes) / (SAMPLE_RATE * 2)
        print(f"{category}_{index}: {duration_s:.2f}s")

    print("done")


if __name__ == "__main__":
    main()
