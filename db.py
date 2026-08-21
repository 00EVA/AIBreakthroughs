#!/usr/bin/env python3
"""
db.py - SQLite layer for the AI Breakthroughs timeline.

The canonical dataset is data/breakthroughs.json. This module mirrors it into
a SQLite DB (data/breakthroughs.db) so the data can be queried with SQL,
exposed via the CLI and the API. If the DB is missing or stale, it rebuilds
from the JSON.

Zero-dependency: uses only the stdlib (sqlite3, json, os).
"""
import json
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
JSON_PATH = os.path.join(DATA_DIR, "breakthroughs.json")
DB_PATH = os.path.join(DATA_DIR, "breakthroughs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS breakthroughs (
    id TEXT PRIMARY KEY,
    year INTEGER,
    date TEXT,
    era TEXT,
    title TEXT,
    org TEXT,
    people TEXT,
    paper TEXT,
    url TEXT,
    what_it_broke TEXT,
    why_impossible TEXT,
    category TEXT,
    impact INTEGER,
    surprise INTEGER,
    tags TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_year ON breakthroughs(year);
CREATE INDEX IF NOT EXISTS idx_era ON breakthroughs(era);
CREATE INDEX IF NOT EXISTS idx_impact ON breakthroughs(impact);
"""


def _now():
    import datetime
    return datetime.datetime.utcnow().isoformat() + "Z"


def load_json():
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _json_mtime():
    return os.path.getmtime(JSON_PATH) if os.path.exists(JSON_PATH) else 0


def _db_mtime():
    return os.path.getmtime(DB_PATH) if os.path.exists(DB_PATH) else 0


def rebuild():
    """Mirror the canonical JSON into SQLite."""
    rows = load_json()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DROP TABLE IF EXISTS breakthroughs")
    conn.executescript(SCHEMA)
    ts = _now()
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO breakthroughs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["id"], r["year"], r["date"], r["era"], r["title"], r["org"],
                json.dumps(r.get("people", [])), r["paper"], r["url"],
                r["what_it_broke"], r["why_impossible"], r["category"],
                r["impact"], r["surprise"], json.dumps(r.get("tags", [])), ts,
            ),
        )
    conn.commit()
    conn.close()
    return len(rows)


def get_conn():
    if not os.path.exists(DB_PATH) or _db_mtime() < _json_mtime():
        rebuild()
    return sqlite3.connect(DB_PATH)


def query(sql, params=()):
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.execute(sql, params)
    results = [dict(r) for r in cur.fetchall()]
    conn.close()
    return results


def all_breakthroughs():
    return query("SELECT * FROM breakthroughs ORDER BY year ASC")


if __name__ == "__main__":
    n = rebuild()
    print(f"Rebuilt {DB_PATH} with {n} breakthroughs")
