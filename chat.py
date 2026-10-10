import os
import argparse
import sys

from dotenv import load_dotenv
from sarvamai import SarvamAI

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="Standalone, billable chat smoke test")
    parser.add_argument("--live", action="store_true")
    if not parser.parse_args().live:
        print("Dry run only. Pass --live to make one paid chat call.")
        return
    api_key = os.environ.get("SARVAM_API_KEY")
    if not api_key:
        sys.exit("SARVAM_API_KEY is not set")
    client = SarvamAI(api_subscription_key=api_key)
    response = client.chat.completions(
        model="sarvam-105b-conversations",
        messages=[{"role": "user", "content": "Hello, who are you?"}],
    )
    print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
