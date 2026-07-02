# tracker/database.py
# Persistence layer. Uses SQLite locally / in tests, and Postgres in
# production when DATABASE_URL is set (e.g. a Neon database on Render), so the
# track record and user accounts survive redeploys instead of being wiped from
# the free tier's ephemeral disk.

import os
import sqlite3
import time
from typing import List, Tuple, Optional

DB_PATH = "data/prices.db"

# Postgres is selected purely by the presence of a postgres DATABASE_URL.
# Render/Neon hand out both "postgres://" and "postgresql://" forms.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
_USE_PG = DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")


def db_backend() -> str:
    """Return the active backend name — surfaced on /health."""
    return "postgres" if _USE_PG else "sqlite"


# ------------------------------------------------------------------------------
# Cross-backend helpers
#
# psycopg2 and sqlite3 differ in three ways we care about:
#   1. Placeholders: sqlite uses "?", psycopg2 uses "%s".
#   2. sqlite's cursor.execute() returns the cursor (chainable .fetchone());
#      psycopg2's returns None, so we never chain.
#   3. New-row id: sqlite exposes cursor.lastrowid; Postgres needs RETURNING id.
# The helpers below paper over all three so the query functions read the same.
# ------------------------------------------------------------------------------

def _connect():
    """Return a DB connection for the active backend."""
    if _USE_PG:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def _q(sql: str) -> str:
    """Translate '?' placeholders to '%s' when talking to Postgres."""
    return sql.replace("?", "%s") if _USE_PG else sql


def _fetchone(cursor, sql: str, params: tuple = ()):
    cursor.execute(_q(sql), params)
    return cursor.fetchone()


def _fetchall(cursor, sql: str, params: tuple = ()):
    cursor.execute(_q(sql), params)
    return cursor.fetchall()


def _execute(cursor, sql: str, params: tuple = ()):
    cursor.execute(_q(sql), params)


def _insert_returning_id(cursor, sql: str, params: tuple):
    """Run an INSERT and return the new row id on either backend."""
    if _USE_PG:
        cursor.execute(_q(sql) + " RETURNING id", params)
        return cursor.fetchone()[0]
    cursor.execute(_q(sql), params)
    return cursor.lastrowid


def _integrity_errors() -> tuple:
    errs = [sqlite3.IntegrityError]
    if _USE_PG:
        import psycopg2
        errs.append(psycopg2.IntegrityError)
    return tuple(errs)


_INTEGRITY_ERRORS = _integrity_errors()

# Backend-specific column fragments for DDL.
_PK = "SERIAL PRIMARY KEY" if _USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
_REAL = "DOUBLE PRECISION" if _USE_PG else "REAL"
_NOCASE = "" if _USE_PG else " COLLATE NOCASE"
_TS_DEFAULT = "TIMESTAMP DEFAULT NOW()" if _USE_PG else "TEXT DEFAULT (datetime('now'))"
_NOW = "NOW()" if _USE_PG else "datetime('now')"


def _column_names(cursor, table: str) -> List[str]:
    """Return existing column names for a table (empty list if it doesn't exist)."""
    if _USE_PG:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        return [row[0] for row in cursor.fetchall()]
    cursor.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cursor.fetchall()]


def _table_exists(cursor, table: str) -> bool:
    if _USE_PG:
        cursor.execute("SELECT to_regclass(%s)", (table,))
        return cursor.fetchone()[0] is not None
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cursor.fetchone() is not None


# ------------------------------------------------------------------------------
# Initialization
# ------------------------------------------------------------------------------

def init_db() -> None:
    """Initialize the database and migrate legacy tables to the per-user schema."""
    conn = _connect()
    cursor = conn.cursor()

    _execute(cursor, f"""
        CREATE TABLE IF NOT EXISTS prices (
            id {_PK},
            symbol TEXT NOT NULL,
            price {_REAL} NOT NULL,
            volume {_REAL},
            timestamp {_REAL} NOT NULL
        )
    """)

    # Accounts.
    _execute(cursor, f"""
        CREATE TABLE IF NOT EXISTS users (
            id            {_PK},
            email         TEXT NOT NULL UNIQUE{_NOCASE},
            password_hash TEXT NOT NULL,
            created_at    {_TS_DEFAULT}
        )
    """)

    _execute(cursor, f"""
        CREATE TABLE IF NOT EXISTS alerts (
            id {_PK},
            user_id INTEGER,
            symbol TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            message TEXT,
            threshold {_REAL},
            multiplier {_REAL},
            zscore {_REAL},
            active INTEGER DEFAULT 1,
            created_at {_REAL} NOT NULL
        )
    """)
    # Legacy alerts table (pre-auth) lacks user_id — add it non-destructively.
    if "user_id" not in _column_names(cursor, "alerts"):
        _execute(cursor, "ALTER TABLE alerts ADD COLUMN user_id INTEGER")

    # Portfolio: per-user, unique per (user, symbol). The pre-auth table had a
    # bare UNIQUE(symbol) constraint that can't be altered in place, so recreate
    # it when migrating. Existing rows are preserved with a NULL owner (they
    # simply won't surface for any signed-in user).
    portfolio_cols = _column_names(cursor, "portfolio")
    if portfolio_cols and "user_id" not in portfolio_cols:
        _execute(cursor, "ALTER TABLE portfolio RENAME TO portfolio_legacy")

    _execute(cursor, f"""
        CREATE TABLE IF NOT EXISTS portfolio (
            id         {_PK},
            user_id    INTEGER,
            symbol     TEXT    NOT NULL{_NOCASE},
            shares     {_REAL} NOT NULL CHECK (shares > 0),
            avg_cost   {_REAL},
            added_at   {_TS_DEFAULT},
            UNIQUE(user_id, symbol)
        )
    """)
    if _table_exists(cursor, "portfolio_legacy"):
        _execute(cursor, """
            INSERT INTO portfolio (user_id, symbol, shares, avg_cost, added_at)
            SELECT NULL, symbol, shares, avg_cost, added_at FROM portfolio_legacy
        """)
        _execute(cursor, "DROP TABLE portfolio_legacy")

    # Per-user persistent watchlist (previously in-memory only).
    _execute(cursor, f"""
        CREATE TABLE IF NOT EXISTS watchlist (
            id       {_PK},
            user_id  INTEGER NOT NULL,
            symbol   TEXT NOT NULL{_NOCASE},
            added_at {_TS_DEFAULT},
            UNIQUE(user_id, symbol)
        )
    """)

    conn.commit()
    conn.close()


# ------------------------------------------------------------------------------
# Users
# ------------------------------------------------------------------------------

def create_user(email: str, password_hash: str) -> Optional[int]:
    """Insert a new user. Returns the new id, or None if the email is taken."""
    conn = _connect()
    cursor = conn.cursor()
    try:
        user_id = _insert_returning_id(
            cursor,
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email.strip().lower(), password_hash),
        )
        conn.commit()
        return user_id
    except _INTEGRITY_ERRORS:
        conn.rollback()
        return None
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[dict]:
    conn = _connect()
    cursor = conn.cursor()
    row = _fetchone(
        cursor,
        "SELECT id, email, password_hash FROM users WHERE email = ?",
        (email.strip().lower(),),
    )
    conn.close()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "password_hash": row[2]}


def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = _connect()
    cursor = conn.cursor()
    row = _fetchone(
        cursor,
        "SELECT id, email, created_at FROM users WHERE id = ?",
        (user_id,),
    )
    conn.close()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "created_at": str(row[2])}


# ------------------------------------------------------------------------------
# Price Storage
# ------------------------------------------------------------------------------

def insert_price(symbol: str, price: float, volume: float = 0.0) -> None:
    """Insert a price row with timestamp."""
    conn = _connect()
    cursor = conn.cursor()
    _execute(
        cursor,
        "INSERT INTO prices (symbol, price, volume, timestamp) VALUES (?, ?, ?, ?)",
        (symbol, price, volume, time.time()),
    )
    conn.commit()
    conn.close()


def get_recent_prices(symbol: str, limit: int = 200) -> List[Tuple[float, float, float]]:
    """
    Return recent prices for a symbol as (timestamp, price, volume) tuples.
    Ordered oldest → newest.
    """
    conn = _connect()
    cursor = conn.cursor()
    rows = _fetchall(
        cursor,
        """
        SELECT timestamp, price, volume
        FROM prices
        WHERE symbol = ?
        ORDER BY timestamp ASC
        LIMIT ?
        """,
        (symbol, limit),
    )
    conn.close()
    return rows


def get_prices_in_range(
    symbol: str,
    start_ts: float,
    end_ts: float,
) -> List[Tuple[float, float, float]]:
    """
    Return prices in a time range as (timestamp, price, volume).
    """
    conn = _connect()
    cursor = conn.cursor()
    rows = _fetchall(
        cursor,
        """
        SELECT timestamp, price, volume
        FROM prices
        WHERE symbol = ?
          AND timestamp >= ?
          AND timestamp <= ?
        ORDER BY timestamp ASC
        """,
        (symbol, start_ts, end_ts),
    )
    conn.close()
    return rows


# ------------------------------------------------------------------------------
# Alerts
# ------------------------------------------------------------------------------

def insert_alert(symbol: str, alert_type: str, message: str) -> None:
    """
    Insert a simple alert (used by tests).
    """
    conn = _connect()
    cursor = conn.cursor()
    _execute(
        cursor,
        """
        INSERT INTO alerts (symbol, alert_type, message, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (symbol, alert_type, message, time.time()),
    )
    conn.commit()
    conn.close()


def create_alert(
    symbol: str,
    alert_type: str,
    threshold: Optional[float] = None,
    multiplier: Optional[float] = None,
    zscore: Optional[float] = None,
    user_id: Optional[int] = None,
) -> int:
    """
    Create a rule-based alert (used by dashboard API), owned by a user.
    """
    conn = _connect()
    cursor = conn.cursor()
    alert_id = _insert_returning_id(
        cursor,
        """
        INSERT INTO alerts (
            user_id, symbol, alert_type, threshold, multiplier, zscore, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, symbol, alert_type, threshold, multiplier, zscore, time.time()),
    )
    conn.commit()
    conn.close()
    return alert_id


def get_recent_alerts(symbol: str, limit: int = 10):
    """
    Return recent alerts for a symbol as (timestamp, alert_type, message).
    """
    conn = _connect()
    cursor = conn.cursor()
    rows = _fetchall(
        cursor,
        """
        SELECT created_at, alert_type, message
        FROM alerts
        WHERE symbol = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (symbol, limit),
    )
    conn.close()
    return rows


def get_alerts(user_id: int):
    """
    Return a user's active alerts (for dashboard listing).
    """
    conn = _connect()
    cursor = conn.cursor()
    rows = _fetchall(
        cursor,
        """
        SELECT id, symbol, alert_type, threshold, multiplier, zscore, created_at
        FROM alerts
        WHERE active = 1 AND user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,),
    )
    conn.close()
    return rows


def delete_alert(alert_id: int, user_id: int) -> None:
    """Delete an alert by id, scoped to its owner."""
    conn = _connect()
    cursor = conn.cursor()
    _execute(
        cursor,
        "DELETE FROM alerts WHERE id = ? AND user_id = ?",
        (alert_id, user_id),
    )
    conn.commit()
    conn.close()


# ------------------------------------------------------------------------------
# Portfolio
# ------------------------------------------------------------------------------

def upsert_holding(
    user_id: int,
    symbol: str,
    shares: float,
    avg_cost: Optional[float] = None,
) -> int:
    """Insert or update a user's holding (unique per user + symbol)."""
    sym = symbol.upper().strip()
    conn = _connect()
    cursor = conn.cursor()
    _execute(
        cursor,
        f"""
        INSERT INTO portfolio (user_id, symbol, shares, avg_cost, added_at)
        VALUES (?, ?, ?, ?, {_NOW})
        ON CONFLICT(user_id, symbol) DO UPDATE SET
            shares   = excluded.shares,
            avg_cost = excluded.avg_cost,
            added_at = excluded.added_at
        """,
        (user_id, sym, shares, avg_cost),
    )
    conn.commit()
    row = _fetchone(
        cursor,
        "SELECT id FROM portfolio WHERE user_id = ? AND symbol = ?",
        (user_id, sym),
    )
    conn.close()
    return row[0]


def get_portfolio(user_id: int) -> List[dict]:
    """Return a user's portfolio holdings ordered by symbol."""
    conn = _connect()
    cursor = conn.cursor()
    rows = _fetchall(
        cursor,
        """
        SELECT id, symbol, shares, avg_cost, added_at
        FROM portfolio
        WHERE user_id = ?
        ORDER BY symbol ASC
        """,
        (user_id,),
    )
    conn.close()
    return [
        {
            "id": r[0],
            "symbol": r[1],
            "shares": r[2],
            "avg_cost": r[3],
            "added_at": str(r[4]),
        }
        for r in rows
    ]


def delete_holding(holding_id: int, user_id: int) -> None:
    """Remove a user's portfolio holding by id (scoped to its owner)."""
    conn = _connect()
    cursor = conn.cursor()
    _execute(
        cursor,
        "DELETE FROM portfolio WHERE id = ? AND user_id = ?",
        (holding_id, user_id),
    )
    conn.commit()
    conn.close()


# ------------------------------------------------------------------------------
# Watchlist (per-user, persistent)
# ------------------------------------------------------------------------------

def get_watchlist(user_id: int) -> List[str]:
    """Return a user's watchlist symbols, newest first."""
    conn = _connect()
    cursor = conn.cursor()
    rows = _fetchall(
        cursor,
        "SELECT symbol FROM watchlist WHERE user_id = ? ORDER BY added_at DESC",
        (user_id,),
    )
    conn.close()
    return [r[0] for r in rows]


def add_to_watchlist(user_id: int, symbol: str) -> None:
    """Add a symbol to a user's watchlist (no-op if already present)."""
    sym = symbol.upper().strip()
    conn = _connect()
    cursor = conn.cursor()
    _execute(
        cursor,
        """
        INSERT INTO watchlist (user_id, symbol) VALUES (?, ?)
        ON CONFLICT(user_id, symbol) DO NOTHING
        """,
        (user_id, sym),
    )
    conn.commit()
    conn.close()


def remove_from_watchlist(user_id: int, symbol: str) -> None:
    """Remove a symbol from a user's watchlist."""
    sym = symbol.upper().strip()
    conn = _connect()
    cursor = conn.cursor()
    _execute(
        cursor,
        "DELETE FROM watchlist WHERE user_id = ? AND symbol = ?",
        (user_id, sym),
    )
    conn.commit()
    conn.close()
