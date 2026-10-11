"""Temporary Docker demo; secrets and state stay in ignored local logs."""
import argparse
import asyncio
import json
import os
import re
import secrets
import subprocess
import time
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "logs" / "local-demo"
STATE = DIRECTORY / "state.json"
ENV_FILE = DIRECTORY / "backend.env"
APP = "sarvam-local-demo"
TUNNEL = "sarvam-local-demo-tunnel"
NETWORK = "sarvam-local-demo-network"
VOLUME = "sarvam-local-demo-budget"
LABEL = "sarvam.local-demo=true"
PORT = 8780


def docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True,
                            timeout=180, cwd=ROOT)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "Docker command failed")
    return result


def owned_container(name):
    result = docker("inspect", name, check=False)
    if result.returncode:
        return False
    item = json.loads(result.stdout)[0]
    if item["Config"].get("Labels", {}).get("sarvam.local-demo") != "true":
        raise RuntimeError(f"Refusing to change unrelated container {name}")
    return True


def stop():
    for name in (TUNNEL, APP):
        if owned_container(name):
            docker("stop", name)
            docker("rm", name)
    if STATE.exists():
        state = json.loads(STATE.read_text())
        state["running"] = False
        STATE.write_text(json.dumps(state, indent=2) + "\n")
    ENV_FILE.unlink(missing_ok=True)
    (DIRECTORY / "PRIVATE-INVITE.md").unlink(missing_ok=True)
    print("Demo stopped. Persistent budget volume retained; allowances not reset.")


def start_app():
    docker("run", "-d", "--name", APP, "--label", LABEL,
           "--network", NETWORK, "--env-file", str(ENV_FILE),
           "-p", f"127.0.0.1:{PORT}:8000", "-v", f"{VOLUME}:/app/logs",
           "sarvam-interpreter:portfolio")


def wait_for_app(live):
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/config", timeout=2) as response:
                if json.load(response)["live_enabled"] == live:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(.5)
    raise RuntimeError("Local demo did not become ready")


def start(stt_seconds=None, unlimited=False, session_seconds=None):
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    stt_seconds = stt_seconds if stt_seconds is not None else previous.get("stt_limit_seconds", 240)
    if stt_seconds < 60:
        raise RuntimeError("STT allowance must cover at least one 60-second session")
    if session_seconds is not None and session_seconds <= 0:
        raise RuntimeError("Session duration must be positive")
    session_seconds = session_seconds if session_seconds is not None else 1800 if unlimited else 60
    duration = str(session_seconds)
    if any(owned_container(name) for name in (APP, TUNNEL)):
        raise RuntimeError("Demo containers already exist; use status or stop first")
    key = os.getenv("SARVAM_API_KEY") or dotenv_values(ROOT / ".env").get("SARVAM_API_KEY")
    if not key or "\n" in key or "\r" in key:
        raise RuntimeError("A valid local SARVAM_API_KEY is required")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    invite = secrets.token_urlsafe(32)
    environment = {
        "SARVAM_API_KEY": key, "DEMO_ACCESS_TOKEN": invite,
        "LIVE_ENABLED": "false", "WEB_CONCURRENCY": "1", "MAX_ROOMS": "1",
        "SESSION_SECONDS": duration, "RECONNECT_SECONDS": "10",
        "MAX_STT_SECONDS": "unlimited" if unlimited else str(stt_seconds),
        "MAX_TRANSLATE_CHARS": "unlimited" if unlimited else "1000",
        "MAX_TTS_CHARS": "unlimited" if unlimited else "1000", "USAGE_DB_PATH": "/app/logs/usage.sqlite3",
        "ALLOWED_ORIGINS": f"http://127.0.0.1:{PORT},http://localhost:{PORT}",
    }

    def save_environment():
        ENV_FILE.write_text("".join(f"{key}={value}\n" for key, value in environment.items()))
        if os.name != "nt":
            ENV_FILE.chmod(0o600)

    if docker("network", "inspect", NETWORK, check=False).returncode:
        docker("network", "create", "--label", LABEL, NETWORK)
    try:
        save_environment()
        start_app()
        wait_for_app(False)
        docker("run", "-d", "--name", TUNNEL, "--label", LABEL,
               "--network", NETWORK, "cloudflare/cloudflared:latest",
               "tunnel", "--no-autoupdate", "--protocol", "http2",
               "--url", f"http://{APP}:8000")
        url = None
        for _ in range(90):
            output = docker("logs", TUNNEL)
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", output.stdout + output.stderr)
            if match:
                url = match.group()
                break
            time.sleep(1)
        if not url:
            raise RuntimeError("Tunnel did not provide a temporary HTTPS URL")
        environment["ALLOWED_ORIGINS"] += f",{url}"
        environment["LIVE_ENABLED"] = "true"
        save_environment()
        docker("stop", APP)
        docker("rm", APP)
        start_app()
        wait_for_app(True)
        STATE.write_text(json.dumps({"url": url, "running": True, "stt_limit_seconds": stt_seconds,
                                     "unlimited_credits": unlimited,
                                     "session_seconds": session_seconds,
                                     "local_url": f"http://127.0.0.1:{PORT}"}, indent=2) + "\n")
        (DIRECTORY / "PRIVATE-INVITE.md").write_text(
            f"# Private temporary demo\n\nURL: {url}\n\nDemo invite: `{invite}`\n\n"
            "Share privately with testers. Do not publish the invite on GitHub or your resume.\n"
            "Keep Docker and this laptop running. Stop after testing:\n\n"
            "`.venv\\Scripts\\python.exe scripts\\local_demo.py stop`\n")
        print(f"Live demo ready: {url}")
        print(f"Private invite: {DIRECTORY / 'PRIVATE-INVITE.md'}")
        if unlimited:
            print(f"Unlimited application credit allowances; session duration: {duration}. Sarvam balance/provider limits still apply.")
        else:
            print(f"{duration}-second speaker sessions; persistent {stt_seconds}s STT / 1000 translation / 1000 TTS allowances.")
        print("No speech calls were made by startup. Stop after testing; do not delete the budget volume.")
    except BaseException:
        stop()
        raise


def status():
    if not STATE.exists():
        print("No local demo has been started.")
        return
    print(STATE.read_text())
    for name in (APP, TUNNEL):
        result = docker("inspect", "--format", "{{.State.Status}}", name, check=False)
        print(f"{name}: {result.stdout.strip() if result.returncode == 0 else 'absent'}")
    if owned_container(APP):
        result = docker("exec", APP, "python", "-c",
                        "import sqlite3; from backend.app import BUDGET; "
                        "BUDGET.reserve('stt_seconds', 0); "
                        "print(sqlite3.connect(BUDGET.path).execute('SELECT kind, amount FROM usage').fetchall())")
        print("Reserved usage:", result.stdout.strip())


def verify():
    import websockets

    state = json.loads(STATE.read_text())
    if not state["running"]:
        raise RuntimeError("Demo is stopped")
    url = state["url"]
    with urllib.request.urlopen(f"{url}/health", timeout=20) as response:
        assert json.load(response)["status"] == "ok"
    with urllib.request.urlopen(f"{url}/config", timeout=20) as response:
        assert json.load(response)["live_enabled"] is True
    invite = dotenv_values(ENV_FILE)["DEMO_ACCESS_TOKEN"]

    async def check_socket():
        endpoint = url.replace("https://", "wss://", 1) + "/ws/connection-check"
        async with websockets.connect(endpoint, origin=url, open_timeout=20) as ws:
            await ws.send(json.dumps({"type": "join", "role": "listener",
                                      "language": "en-IN", "access_token": "invalid"}))
            assert json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] == "error"
        async with websockets.connect(endpoint, origin=url, open_timeout=20) as ws:
            await ws.send(json.dumps({"type": "join", "role": "listener",
                                      "language": "en-IN", "access_token": invite}))
            assert json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] == "joined"
            assert json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] == "ready"
            await ws.send(json.dumps({"type": "ping", "client_ms": 123}))
            assert json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] == "pong"
            await ws.send(json.dumps({"type": "end"}))

    asyncio.run(check_socket())
    print("Public HTTPS, live flag, invalid-invite rejection and listener WebSocket ping passed.")
    print("Listener-only verification opened no upstream STT session.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop", "status", "verify"))
    parser.add_argument("--live", action="store_true", help="Explicitly enable billable speech sessions")
    parser.add_argument("--stt-seconds", type=int, help="Lifetime STT allowance; preserves usage, not a reset")
    parser.add_argument("--unlimited", action="store_true", help="Remove cumulative speech/translation/TTS caps; requires spending approval")
    parser.add_argument("--session-seconds", type=int, help="Explicit per-join duration, even with unlimited credit caps")
    args = parser.parse_args()
    if args.command == "start" and not args.live:
        parser.error("start requires --live; this exposes invite-protected billable access")
    try:
        if args.command == "start":
            start(args.stt_seconds, unlimited=args.unlimited, session_seconds=args.session_seconds)
        else:
            {"stop": stop, "status": status, "verify": verify}[args.command]()
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(str(error)) from None
