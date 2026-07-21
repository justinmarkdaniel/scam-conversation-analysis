"""SQLite persistence keyed by conversation_id. Single file, zero-config,
parameterised queries (so injected message text can't reach SQL)."""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "data.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with _conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS analyses (
                conversation_id  TEXT PRIMARY KEY,
                content_hash     TEXT NOT NULL,
                scam_probability REAL,
                intel_json       TEXT NOT NULL,
                llm_status       TEXT NOT NULL,
                updated_at       TEXT NOT NULL
            )"""
        )


def get(conversation_id: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM analyses WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "conversation_id": row["conversation_id"],
        "content_hash": row["content_hash"],
        "scam_probability": row["scam_probability"],
        "intel": json.loads(row["intel_json"]),
        "llm_status": row["llm_status"],
    }


def upsert(
    conversation_id: str,
    content_hash: str,
    scam_probability: Optional[float],
    intel: dict,
    llm_status: str,
    updated_at: str,
) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO analyses
                 (conversation_id, content_hash, scam_probability, intel_json,
                  llm_status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                 content_hash=excluded.content_hash,
                 scam_probability=excluded.scam_probability,
                 intel_json=excluded.intel_json,
                 llm_status=excluded.llm_status,
                 updated_at=excluded.updated_at""",
            (conversation_id, content_hash, scam_probability,
             json.dumps(intel), llm_status, updated_at),
        )
