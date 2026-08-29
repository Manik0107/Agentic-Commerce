import json
import os
import sqlite3

DB_PATH = os.getenv("AC_DB", "agentic_commerce.db")

SCHEMA = """
-- Flat, not nested: SQLite indexes columns, not JSON paths. catalog.py rebuilds
-- the nested product shape on read.
CREATE TABLE IF NOT EXISTS variants (
    sku                     TEXT PRIMARY KEY,
    product_id              TEXT NOT NULL,
    merchant_id             TEXT NOT NULL,
    name                    TEXT NOT NULL,
    category_canonical      TEXT NOT NULL,
    category_raw            TEXT NOT NULL,
    category_confidence     REAL NOT NULL,
    color_canonical         TEXT,
    color_shade             TEXT,
    color_raw               TEXT,
    color_confidence        REAL,
    material_canonical      TEXT,
    material_raw            TEXT,
    material_confidence     REAL,
    size                    TEXT,
    price_paise             INTEGER NOT NULL,
    currency                TEXT NOT NULL DEFAULT 'INR',
    availability_status     TEXT NOT NULL,
    availability_checked_at TEXT NOT NULL,
    last_updated            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_variants_cat   ON variants(merchant_id, category_canonical);
CREATE INDEX IF NOT EXISTS ix_variants_color ON variants(color_canonical, color_shade);
CREATE INDEX IF NOT EXISTS ix_variants_price ON variants(price_paise);
CREATE INDEX IF NOT EXISTS ix_variants_avail ON variants(availability_status);

-- Keeps a full sync inside the Gemini free tier (20 requests/key/model/day):
-- hundreds of products share only a few dozen distinct attribute strings.
CREATE TABLE IF NOT EXISTS norm_cache (
    attribute   TEXT NOT NULL,
    raw         TEXT NOT NULL,
    canonical   TEXT NOT NULL,
    shade       TEXT,
    confidence  REAL NOT NULL,
    source      TEXT NOT NULL,          -- 'gemini' | 'difflib' | 'exact'
    PRIMARY KEY (attribute, raw)
);

-- Scoped payment token (PRD 8.2). Allowlists are JSON text: never filtered on in
-- SQL, only read whole and checked in Python.
CREATE TABLE IF NOT EXISTS tokens (
    token_id           TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    spend_cap_paise    INTEGER NOT NULL,
    currency           TEXT NOT NULL DEFAULT 'INR',
    merchant_allowlist TEXT NOT NULL,   -- json array
    category_allowlist TEXT NOT NULL,   -- json array, empty = no category restriction
    valid_until        TEXT NOT NULL,   -- ISO 8601
    used_amount_paise  INTEGER NOT NULL DEFAULT 0
);

-- Append-only. Every payment attempt lands here, allowed or blocked, before the
-- caller gets a response.
CREATE TABLE IF NOT EXISTS audit (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    token_id          TEXT,
    agent_id          TEXT,
    merchant_id       TEXT,
    product_id        TEXT,
    sku               TEXT,
    amount_paise      INTEGER,
    protocol_used     TEXT,
    gate_decision     TEXT NOT NULL,    -- 'allowed' | 'blocked' | 'error'
    reason            TEXT,
    razorpay_order_id TEXT,
    raw_intent        TEXT              -- original protocol payload, for replay
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # WAL: read the audit log while a sync is writing.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


def jdump(value) -> str:
    """JSON with sorted keys, so identical allowlists serialise identically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    init()
    print(f"initialised {DB_PATH}")
