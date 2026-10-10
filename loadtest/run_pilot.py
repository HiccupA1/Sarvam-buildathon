"""Launch an isolated, bounded pilot server; never print credentials."""
import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def stop_service(service):
    if service.poll() is None:
        if os.name == "nt":
            # The Windows venv launcher can have a real Python child process.
            subprocess.run(["taskkill", "/PID", str(service.pid), "/T", "/F"],
                           capture_output=True, timeout=10, check=False)
        else:
            service.terminate()
    try:
        service.wait(timeout=10)
    except subprocess.TimeoutExpired:
        service.kill()
        service.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--full", action="store_true", help="Expand to all 92 clips under a separate allowance")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    test_args = []
    if args.full:
        test_args = ["--per-category", "20", "--max-utterances", "92", "--max-input-seconds", "400",
                     "--max-source-chars", "5000", "--min-utterance-seconds", "10",
                     "--output", "loadtest/results/structured_test_campaign.json"]
    if not args.live:
        return subprocess.call([sys.executable, "loadtest/run_structured_test.py", *test_args], cwd=root)
    env = dict(os.environ, LIVE_ENABLED="true", DEMO_ACCESS_TOKEN=secrets.token_urlsafe(32),
               SESSION_SECONDS="45", MAX_STT_SECONDS="225", MAX_TRANSLATE_CHARS="4000",
               MAX_TTS_CHARS="8000", USAGE_DB_PATH=str(root / "logs" / "pilot_usage.sqlite3"))
    if args.full:
        env.update(MAX_STT_SECONDS="4140", MAX_TRANSLATE_CHARS="40000", MAX_TTS_CHARS="60000",
                   USAGE_DB_PATH=str(root / "logs" / "campaign_usage.sqlite3"))
        pilot = root / "loadtest/results/structured_test_pilot.json"
        campaign = root / "loadtest/results/structured_test_campaign.json"
        if pilot.exists() and not campaign.exists():
            campaign.write_text(json.dumps(json.loads(pilot.read_text(encoding="utf-8")), indent=2, ensure_ascii=False), encoding="utf-8")
    # Reserve limits correspond to about INR 33.88 excluding GST at documented rates.
    root.joinpath("logs").mkdir(exist_ok=True)
    with root.joinpath("logs", "pilot_server.log").open("a", encoding="utf-8") as log:
        service = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1",
                                    "--port", "8765", "--workers", "1", "--ws-max-size", "65536"],
                                   cwd=root, env=env, stdout=log, stderr=log)
        try:
            for _ in range(30):
                if service.poll() is not None:
                    raise RuntimeError("Pilot server stopped; see logs/pilot_server.log")
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1):
                        break
                except OSError:
                    time.sleep(.5)
            else:
                raise RuntimeError("Pilot server did not become ready")
            return subprocess.call([sys.executable, "-u", "loadtest/run_structured_test.py", "--live",
                                    "--server-url", "ws://127.0.0.1:8765", *test_args], cwd=root, env=env)
        finally:
            stop_service(service)


if __name__ == "__main__":
    raise SystemExit(main())
