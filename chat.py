import os
import sys

from dotenv import load_dotenv
from sarvamai import SarvamAI

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

api_key = os.environ.get("SARVAM_API_KEY")
if not api_key:
    sys.exit("SARVAM_API_KEY is not set. Add it to .env and try again.")

client = SarvamAI(api_subscription_key=api_key)

response = client.chat.completions(
    model="sarvam-105b-conversations",
    messages=[{"role": "user", "content": "Hello, who are you?"}],
)

print(response.choices[0].message.content)
