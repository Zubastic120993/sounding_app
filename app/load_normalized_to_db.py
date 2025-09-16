
#!/usr/bin/env python3
import os
import glob
import sqlite3
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(__file__))  # project root
IN_DIR = os.path.join(ROOT, "data", "tanks_csv", "normalized")
DB_PATH = os.path.join(ROOT, "data", "sounding.db")

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  sounding_cm REAL,      -- nullable if ullage-based
  ullage_cm REAL,        -- nullable if sounding-based
  trim TEXT,             -- e.g. "-6", "-4", "0", "2"
  heel TEXT,             -- e.g. "P2","P1","S-1","S-2"
  volume_m3 REAL,        -- base or trimmed volume
  correction_m3 REAL     -- heel correction; volume_m3 stays NULL for heel rows
);

CREATE INDEX IF NOT EXISTS idx_readings_name ON readings(name);
CREATE INDEX IF NOT EXISTS idx_readings_name_snd_trim ON readings(name, sounding_cm, trim);
CREATE INDEX IF NOT EXISTS idx_readings_name_ull_trim ON readings(name, ullage_cm, trim);
CREATE INDEX IF NOT EXISTS idx_readings_name_snd_heel ON readings(name, sounding_cm, heel);
CREATE INDEX IF NOT EXISTS idx_readings_name_ull_heel ON readings(name, ullage_cm, heel);
"""

REQUIRED_COLS = [
    "name","sounding_cm","ullage_cm","trim","heel","volume_m3","correction_m3"
]

def load_one_csv(path: str, conn: sqlite3.Connection):
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{os.path.basename(path)} missing columns: {missing}")

    for col in ["sounding_cm", "ullage_cm", "volume_m3", "correction_m3"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["trim", "heel", "name"]:
        df[col] = df[col].astype("string")

    df.to_sql("readings", conn, if_exists="append", index=False)
    print(f"Loaded {len(df)} rows from {os.path.basename(path)}")

def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)  # <-- THIS is the key change

    csvs = sorted(glob.glob(os.path.join(IN_DIR, "*_normalized.csv")))
    if not csvs:
        print(f"No *_normalized.csv found in {IN_DIR}")
        return

    with conn:
        conn.execute("DELETE FROM readings;")

    for f in csvs:
        load_one_csv(f, conn)

    cur = conn.execute("SELECT name, COUNT(*) FROM readings GROUP BY name ORDER BY name;")
    print("\nSummary (rows per tank):")
    for name, cnt in cur.fetchall():
        print(f"  {name}: {cnt}")

    conn.close()
    print(f"\n✅ Database ready at: {DB_PATH}")

if __name__ == "__main__":
    main()


    