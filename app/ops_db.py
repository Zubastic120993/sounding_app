
#!/usr/bin/env python3
import os
import sqlite3
from datetime import datetime, timezone

# ops database lives alongside sounding.db in /data
OPS_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "ops.db")

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS tanks (
  name            TEXT PRIMARY KEY,
  product         TEXT,
  density15_kg_m3 REAL,
  mode            TEXT CHECK (mode IN ('sounding','ullage')) DEFAULT 'sounding',
  notes           TEXT
);

CREATE TABLE IF NOT EXISTS schedules (
  tank_name   TEXT REFERENCES tanks(name) ON DELETE CASCADE,
  frequency   TEXT CHECK (frequency IN ('daily','weekly','adhoc')) NOT NULL,
  UNIQUE(tank_name)
);

CREATE TABLE IF NOT EXISTS sessions (
  id          INTEGER PRIMARY KEY,
  kind        TEXT CHECK (kind IN ('bunkering','transfer','survey')) NOT NULL,
  started_at  TEXT NOT NULL,
  ended_at    TEXT,
  title       TEXT,
  counterparty TEXT,
  remarks     TEXT
);

CREATE TABLE IF NOT EXISTS readings (
  id               INTEGER PRIMARY KEY,
  ts               TEXT NOT NULL,
  tank_name        TEXT NOT NULL REFERENCES tanks(name),
  session_id       INTEGER REFERENCES sessions(id),

  mode             TEXT CHECK (mode IN ('sounding','ullage')) NOT NULL,
  sounding_cm      REAL,
  ullage_cm        REAL,
  trim             REAL,
  heel_label       TEXT,
  temperature_c    REAL,
  density15_kg_m3  REAL,

  base_vol_m3      REAL,
  heel_corr_m3     REAL,
  volume_obs_m3    REAL,
  vcf              REAL,
  volume_15c_m3    REAL,
  mass_kg          REAL,
  note             TEXT
);

CREATE INDEX IF NOT EXISTS idx_readings_tank_ts ON readings(tank_name, ts);
CREATE INDEX IF NOT EXISTS idx_readings_session ON readings(session_id);
"""

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def connect() -> sqlite3.Connection:
    """Create/open ops.db and ensure schema exists. Returns a connection."""
    os.makedirs(os.path.join(os.path.dirname(OPS_DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(OPS_DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    with conn:
        for stmt in SCHEMA.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s + ";")
    return conn

# Convenience helpers (we’ll use these from the UI/CLI later)

def ensure_tank(conn: sqlite3.Connection, name: str,
                product: str | None = None,
                density15_kg_m3: float | None = None,
                mode: str = "sounding",
                notes: str | None = None):
    with conn:
        conn.execute(
            """INSERT INTO tanks(name, product, density15_kg_m3, mode, notes)
               VALUES(?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 product=COALESCE(excluded.product, tanks.product),
                 density15_kg_m3=COALESCE(excluded.density15_kg_m3, tanks.density15_kg_m3),
                 mode=COALESCE(excluded.mode, tanks.mode),
                 notes=COALESCE(excluded.notes, tanks.notes);""",
            (name, product, density15_kg_m3, mode, notes)
        )

def set_schedule(conn: sqlite3.Connection, tank_name: str, frequency: str):
    with conn:
        conn.execute(
            """INSERT INTO schedules(tank_name, frequency)
               VALUES(?,?)
               ON CONFLICT(tank_name) DO UPDATE SET frequency=excluded.frequency;""",
            (tank_name, frequency)
        )

def start_session(conn: sqlite3.Connection, kind: str,
                  title: str | None = None,
                  counterparty: str | None = None,
                  remarks: str | None = None) -> int:
    with conn:
        cur = conn.execute(
            "INSERT INTO sessions(kind, started_at, title, counterparty, remarks) VALUES(?,?,?,?,?)",
            (kind, utcnow_iso(), title, counterparty, remarks)
        )
        return cur.lastrowid

def end_session(conn: sqlite3.Connection, session_id: int):
    with conn:
        conn.execute("UPDATE sessions SET ended_at=? WHERE id=? AND ended_at IS NULL",
                     (utcnow_iso(), session_id))

def add_reading(conn: sqlite3.Connection,
                tank_name: str,
                mode: str,
                sounding_cm: float | None,
                ullage_cm: float | None,
                trim: float,
                heel_label: str | None,
                temperature_c: float | None,
                density15_kg_m3: float | None,
                base_vol_m3: float,
                heel_corr_m3: float,
                volume_obs_m3: float,
                vcf: float | None,
                volume_15c_m3: float | None,
                mass_kg: float | None,
                note: str | None = None,
                session_id: int | None = None):
    with conn:
        conn.execute(
            """INSERT INTO readings
               (ts, tank_name, session_id, mode, sounding_cm, ullage_cm, trim, heel_label,
                temperature_c, density15_kg_m3, base_vol_m3, heel_corr_m3, volume_obs_m3,
                vcf, volume_15c_m3, mass_kg, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (utcnow_iso(), tank_name, session_id, mode, sounding_cm, ullage_cm, trim, heel_label,
             temperature_c, density15_kg_m3, base_vol_m3, heel_corr_m3, volume_obs_m3,
             vcf, volume_15c_m3, mass_kg, note)
        )


def close_session(conn: sqlite3.Connection, session_id: int, ended_at: str | None = None, note: str | None = None):
    """
    Mark a session as ended. If ended_at is None, use current timestamp.
    If note is provided, append it to the session note (with a newline if needed).
    """
    # End timestamp
    if ended_at is None:
        conn.execute(
            "UPDATE sessions SET closed_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (session_id,)
        )
    else:
        conn.execute(
            "UPDATE sessions SET closed_at = ? WHERE id = ?;",
            (ended_at, session_id)
        )

    # Optional note append
    if note:
        row = conn.execute(
            "SELECT note FROM sessions WHERE id = ?;",
            (session_id,)
        ).fetchone()
        prev = row[0] if row and row[0] else ""
        new_note = (prev + ("\n" if prev else "") + note)
        conn.execute(
            "UPDATE sessions SET note = ? WHERE id = ?;",
            (new_note, session_id)
        )

    conn.commit()

if __name__ == "__main__":
    # Create/ensure schema and show where the DB is.
    conn = connect()
    conn.close()
    print(f"✅ ops.db initialized at: {OPS_DB_PATH}")