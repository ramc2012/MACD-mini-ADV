"""Fresh prospective observations. No portfolio, fill, order, or legacy-book writes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def encoded(value):
    return json.dumps(value, sort_keys=True, default=str, allow_nan=False, separators=(",", ":"))


class ShadowStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS publications (
                id TEXT PRIMARY KEY, lane TEXT NOT NULL, session TEXT NOT NULL,
                decision_at TEXT NOT NULL, policy TEXT NOT NULL, payload TEXT NOT NULL,
                UNIQUE(lane,session));
              CREATE TABLE IF NOT EXISTS status (
                lane TEXT PRIMARY KEY, checked_at TEXT NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, lane TEXT NOT NULL, at TEXT NOT NULL,
                kind TEXT NOT NULL, detail TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS outcomes (
                publication_id TEXT NOT NULL, instrument TEXT NOT NULL, horizon TEXT NOT NULL,
                observed_at TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(publication_id,instrument,horizon));
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def status(self, lane, at, payload):
        body = encoded(payload)
        with self.connect() as db:
            previous = db.execute("SELECT payload FROM status WHERE lane=?", (lane,)).fetchone()
            db.execute("INSERT INTO status VALUES(?,?,?) ON CONFLICT(lane) DO UPDATE SET checked_at=excluded.checked_at,payload=excluded.payload",
                       (lane, str(at), body))
            # Readiness changes are journaled once; polling never creates a stream of duplicate decisions.
            key = lambda x: encoded({k: x.get(k) for k in ("status", "blockers", "source_session")})
            if previous is None or key(json.loads(previous[0])) != key(payload):
                db.execute("INSERT INTO events(lane,at,kind,detail) VALUES(?,?,?,?)", (lane, str(at), "readiness", body))

    def publish(self, lane, session, at, policy, payload):
        if payload.get("status") != "ok" or not payload.get("watchlist"):
            raise ValueError("only a nonempty, ready prospective observation can freeze")
        body = encoded({**payload, "watchlist": [{**r, "actionable": False} for r in payload["watchlist"]],
                        "shadow_only": True, "execution_enabled": False})
        identity = hashlib.sha256(f"{lane}:{session}".encode()).hexdigest()[:24]
        with self.connect() as db:
            inserted = db.execute("INSERT OR IGNORE INTO publications VALUES(?,?,?,?,?,?)",
                                  (identity, lane, str(session), str(at), policy, body)).rowcount
            if inserted:
                db.execute("INSERT INTO events(lane,at,kind,detail) VALUES(?,?,?,?)",
                           (lane, str(at), "frozen_observation", encoded({"id": identity, "session": str(session), "policy": policy})))
        return bool(inserted)

    def publications(self, lane=None):
        with self.connect() as db:
            rows = db.execute("SELECT id,lane,session,decision_at,policy,payload FROM publications WHERE (? IS NULL OR lane=?) ORDER BY decision_at DESC LIMIT 100",
                              (lane, lane)).fetchall()
        return [{**json.loads(r[5]), "id": r[0], "lane": r[1], "source_session": r[2], "decision_at": r[3], "policy": r[4]} for r in rows]

    def record_outcome(self, publication, instrument, horizon, at, payload):
        if payload.get("baseline_price", 0) <= 0 or payload.get("observed_price", 0) <= 0:
            raise ValueError("an observed return needs two real prices")
        with self.connect() as db:
            return bool(db.execute("INSERT OR IGNORE INTO outcomes VALUES(?,?,?,?,?)",
                                   (publication, instrument, horizon, str(at), encoded(payload))).rowcount)

    def snapshot(self, lane):
        with self.connect() as db:
            state = db.execute("SELECT checked_at,payload FROM status WHERE lane=?", (lane,)).fetchone()
            events = db.execute("SELECT at,kind,detail FROM events WHERE lane=? ORDER BY id DESC LIMIT 100", (lane,)).fetchall()
            outcomes = db.execute("SELECT instrument,horizon,observed_at,payload FROM outcomes WHERE publication_id IN (SELECT id FROM publications WHERE lane=?) ORDER BY observed_at DESC LIMIT 500", (lane,)).fetchall()
        return {"lane": lane, "shadow_only": True, "execution_enabled": False,
                "checked_at": state[0] if state else None,
                "readiness": json.loads(state[1]) if state else {"status": "awaiting_first_check", "blockers": ["Worker has not checked inputs yet"]},
                "publications": self.publications(lane),
                "journal": [dict(at=a, kind=k, detail=json.loads(d)) for a, k, d in events],
                "outcomes": [dict(instrument=i, horizon=h, observed_at=a, **json.loads(p)) for i, h, a, p in outcomes],
                "method": "Prospective quote-to-quote observations before costs; no simulated fills or validated edge"}
