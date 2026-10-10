"""Durable, conservative reservations; never a substitute for provider billing."""
import os
import sqlite3
from contextlib import closing
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


class UsageBudget:
    def __init__(self, path, limits):
        self.path = Path(path)
        self.limits = limits

    def reserve(self, kind, amount):
        if amount < 0 or kind not in self.limits:
            raise ValueError("invalid reservation")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS usage (kind TEXT PRIMARY KEY, amount REAL NOT NULL)")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT amount FROM usage WHERE kind = ?", (kind,)).fetchone()
            used = row[0] if row else 0
            if used + amount > self.limits[kind]:
                raise BudgetExceeded(f"Demo {kind} allowance exhausted")
            db.execute("INSERT INTO usage VALUES (?, ?) ON CONFLICT(kind) DO UPDATE SET amount = excluded.amount",
                       (kind, used + amount))


def configured_budget():
    return UsageBudget(os.getenv("USAGE_DB_PATH", "logs/usage.sqlite3"), {
        "stt_seconds": float(os.getenv("MAX_STT_SECONDS", "1800")),
        "translate_chars": int(os.getenv("MAX_TRANSLATE_CHARS", "20000")),
        "tts_chars": int(os.getenv("MAX_TTS_CHARS", "20000")),
    })
