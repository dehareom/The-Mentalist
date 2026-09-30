"""SQLite storage for alerts and activity events. No video or audio is ever stored."""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "elderguard.db"
OPEN_STATUSES = ("PENDING", "VERIFICATION", "ACTIVE", "ESCALATED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT NOT NULL,      -- FALL | VOICE_SOS | INACTIVITY | MANUAL_SOS
    severity        TEXT NOT NULL,      -- HIGH | MEDIUM
    timestamp       TEXT NOT NULL,
    status          TEXT NOT NULL,      -- PENDING | VERIFICATION | ACTIVE | ACKNOWLEDGED | RESOLVED | ESCALATED | CANCELLED
    message         TEXT,
    acknowledged_at TEXT,
    resolved_at     TEXT,
    resolution      TEXT                -- human-readable outcome shown in history
);
CREATE TABLE IF NOT EXISTS activity_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    timestamp  TEXT NOT NULL,
    duration   REAL,                    -- seconds, when it applies
    status     TEXT
);
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run(sql, params=(), fetch=False):
    """Open a short-lived connection (safe to call from any thread)."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        result = [dict(r) for r in cur.fetchall()] if fetch else cur.lastrowid
        conn.commit()
        return result
    finally:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        # An alert left open by a previous run can never be answered, so close it.
        marks = ",".join("?" * len(OPEN_STATUSES))
        conn.execute(
            f"UPDATE alerts SET status='CANCELLED', resolved_at=?, "
            f"resolution='Interrupted (app restarted)' WHERE status IN ({marks})",
            (now_str(), *OPEN_STATUSES),
        )
        conn.commit()
    finally:
        conn.close()


def create_alert(kind, severity, status, message):
    return _run(
        "INSERT INTO alerts (type, severity, timestamp, status, message) VALUES (?,?,?,?,?)",
        (kind, severity, now_str(), status, message),
    )


def update_alert(alert_id, **fields):
    allowed = {"status", "acknowledged_at", "resolved_at", "resolution", "message"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        _run(f"UPDATE alerts SET {sets} WHERE id=?", (*fields.values(), alert_id))


def get_alerts(limit=50):
    return _run("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,), fetch=True)


def log_event(event_type, status="INFO", duration=None):
    _run(
        "INSERT INTO activity_events (event_type, timestamp, duration, status) VALUES (?,?,?,?)",
        (event_type, now_str(), duration, status),
    )


def get_history(limit=50):
    """Alerts and activity events merged into one newest-first list."""
    rows = [{**a, "kind": "alert"} for a in get_alerts(limit)]
    for e in _run("SELECT * FROM activity_events ORDER BY id DESC LIMIT ?", (limit,), fetch=True):
        rows.append({
            "kind": "activity", "id": e["id"], "type": e["event_type"], "timestamp": e["timestamp"],
            "severity": "INFO", "status": e["status"], "duration": e["duration"],
            "resolution": None, "acknowledged_at": None, "message": None,
        })
    rows.sort(key=lambda r: (r["timestamp"], r["id"]), reverse=True)
    return rows[:limit]
