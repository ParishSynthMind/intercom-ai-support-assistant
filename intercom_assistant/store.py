"""Local SQLite state: what was handled, the draft queue, and the opt-out list.

Idempotency works on two keys:
- the webhook notification id (Intercom retries a delivery it thinks failed),
- the conversation part id of the customer message (two topics, a manual
  `process` run and a poll can all point at the same message).
A message is drafted at most once, and a draft is sent at most once.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    notification_id TEXT PRIMARY KEY,
    topic TEXT, received_at INTEGER
);
CREATE TABLE IF NOT EXISTS handled (
    message_key TEXT PRIMARY KEY,
    conversation_id TEXT, outcome TEXT, at INTEGER
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_key TEXT UNIQUE,
    conversation_id TEXT NOT NULL,
    status TEXT NOT NULL,             -- pending, approved, sent, rejected, failed
    body TEXT NOT NULL,
    meta TEXT NOT NULL,
    note_part_id TEXT, reply_part_id TEXT,
    decided_by TEXT, created_at INTEGER, decided_at INTEGER
);
CREATE TABLE IF NOT EXISTS suppressed (
    contact_id TEXT PRIMARY KEY,
    reason TEXT, at INTEGER
);
CREATE TABLE IF NOT EXISTS tags (
    name TEXT PRIMARY KEY, tag_id TEXT
);
"""


@dataclass
class Draft:
    id: int
    message_key: str
    conversation_id: str
    status: str
    body: str
    meta: dict
    note_part_id: str | None
    reply_part_id: str | None
    decided_by: str | None


class Store:
    def __init__(self, path: str | Path = ":memory:", clock=time.time):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL") if str(path) != ":memory:" else None
        self.db.executescript(SCHEMA)
        self.clock = clock

    def _now(self) -> int:
        return int(self.clock())

    # --- webhook deliveries ----------------------------------------------
    def first_delivery(self, notification_id: str, topic: str) -> bool:
        """True the first time a notification id is seen, False for a retry/duplicate."""
        cur = self.db.execute("INSERT OR IGNORE INTO events VALUES (?, ?, ?)",
                              (notification_id, topic, self._now()))
        return cur.rowcount == 1

    # --- handled messages -------------------------------------------------
    def claim(self, message_key: str, conversation_id: str) -> bool:
        """Atomically claim a customer message for processing. False if already claimed."""
        cur = self.db.execute("INSERT OR IGNORE INTO handled VALUES (?, ?, 'processing', ?)",
                              (message_key, conversation_id, self._now()))
        return cur.rowcount == 1

    def finish(self, message_key: str, outcome: str) -> None:
        self.db.execute("UPDATE handled SET outcome = ?, at = ? WHERE message_key = ?",
                        (outcome, self._now(), message_key))

    def release(self, message_key: str) -> None:
        """Undo a claim after a failure, so a retry can process the message again."""
        self.db.execute("DELETE FROM handled WHERE message_key = ? AND outcome = 'processing'", (message_key,))

    def outcome(self, message_key: str) -> str | None:
        row = self.db.execute("SELECT outcome FROM handled WHERE message_key = ?", (message_key,)).fetchone()
        return row[0] if row else None

    # --- drafts -------------------------------------------------------------
    def add_draft(self, message_key: str, conversation_id: str, body: str, meta: dict,
                  note_part_id: str | None) -> int:
        cur = self.db.execute(
            "INSERT INTO drafts (message_key, conversation_id, status, body, meta, note_part_id, created_at) "
            "VALUES (?, ?, 'pending', ?, ?, ?, ?)",
            (message_key, conversation_id, body, json.dumps(meta, sort_keys=True), note_part_id, self._now()))
        return int(cur.lastrowid)

    def get_draft(self, draft_id: int) -> Draft | None:
        row = self.db.execute("SELECT id, message_key, conversation_id, status, body, meta, note_part_id, "
                              "reply_part_id, decided_by FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if not row:
            return None
        return Draft(row[0], row[1], row[2], row[3], row[4], json.loads(row[5]), row[6], row[7], row[8])

    def list_drafts(self, status: str | None = "pending") -> list[Draft]:
        q = "SELECT id FROM drafts" + (" WHERE status = ?" if status else "") + " ORDER BY id"
        ids = [r[0] for r in self.db.execute(q, (status,) if status else ())]
        return [self.get_draft(i) for i in ids]

    def transition(self, draft_id: int, from_status: str, to_status: str, by: str | None = None) -> bool:
        """Compare-and-set, so two approvers clicking at once cannot both send."""
        cur = self.db.execute(
            "UPDATE drafts SET status = ?, decided_by = COALESCE(?, decided_by), decided_at = ? "
            "WHERE id = ? AND status = ?", (to_status, by, self._now(), draft_id, from_status))
        return cur.rowcount == 1

    def set_reply_part(self, draft_id: int, part_id: str) -> None:
        self.db.execute("UPDATE drafts SET reply_part_id = ? WHERE id = ?", (part_id, draft_id))

    def drafts_today(self) -> int:
        start = self._now() - (self._now() % 86400)
        return self.db.execute("SELECT COUNT(*) FROM drafts WHERE created_at >= ?", (start,)).fetchone()[0]

    # --- opt-outs -------------------------------------------------------------
    def suppress(self, contact_id: str, reason: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO suppressed VALUES (?, ?, ?)", (contact_id, reason, self._now()))

    def unsuppress(self, contact_id: str) -> None:
        self.db.execute("DELETE FROM suppressed WHERE contact_id = ?", (contact_id,))

    def is_suppressed(self, contact_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM suppressed WHERE contact_id = ?", (contact_id,)).fetchone() is not None

    # --- tag id cache ---------------------------------------------------------
    def tag_id(self, name: str) -> str | None:
        row = self.db.execute("SELECT tag_id FROM tags WHERE name = ?", (name.lower(),)).fetchone()
        return row[0] if row else None

    def save_tag(self, name: str, tag_id: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO tags VALUES (?, ?)", (name.lower(), tag_id))
