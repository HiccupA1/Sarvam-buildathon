"""One-time script: synthesizes one short speech clip per language via
Sarvam TTS and saves it as raw PCM16 mono 16kHz. The load test streams
these files as simulated mic input, so it never depends on an actual
microphone or human speaker.

Run once: python loadtest/generate_fixtures.py
"""
import base64
import os

from dotenv import load_dotenv
from sarvamai import SarvamAI

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SAMPLE_RATE = 16000

load_dotenv(os.path.join(PROJECT_DIR, ".env"))
API_KEY = os.environ["SARVAM_API_KEY"]

CLIPS = {
    "hindi": ("hi-IN", "नमस्ते, मैं आज सुबह बाजार गया था कुछ सब्जियां खरीदने के लिए।"),
    "english": ("en-IN", "Hello, I went to the market this morning to buy some vegetables."),
    "tamil": ("ta-IN", "வணக்கம், நான் இன்று காலை சந்தைக்கு காய்கறிகள் வாங்க சென்றேன்."),
    "telugu": ("te-IN", "నమస్తే, నేను ఈ రోజు ఉదయం కూరగాయలు కొనడానికి మార్కెట్‌కు వెళ్ళాను."),
}


def main():
    os.makedirs(FIXTURES_DIR, exist_ok=True)
    client = SarvamAI(api_subscription_key=API_KEY)

    for name, (language_code, text) in CLIPS.items():
        response = client.text_to_speech.convert(
            text=text,
            language_code=language_code,
            speaker="kavitha",
            model="bulbul:v3",
            output_audio_codec="linear16",
            speech_sample_rate=SAMPLE_RATE,
        )
        pcm_bytes = base64.b64decode(response.audios[0])
        out_path = os.path.join(FIXTURES_DIR, f"{name}.pcm")
        with open(out_path, "wb") as f:
            f.write(pcm_bytes)
        duration_s = len(pcm_bytes) / (SAMPLE_RATE * 2)
        print(f"{name}: {len(pcm_bytes)} bytes, {duration_s:.2f}s -> {out_path}")


if __name__ == "__main__":
    main()
