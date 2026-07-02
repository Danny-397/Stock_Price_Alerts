# tracker/database.py
# SQLite persistence for prices and alerts.

import os
import sqlite3
import time
from typing import List, Tuple, Optional

DB_PATH = "data/prices.db"


# ------------------------------------------------------------------------------
# Internal Helpers
# ------------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    """Ensure DB directory exists and return a connection."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


# ------------------------------------------------------------------------------
# Initialization
# ------------------------------------------------------------------------------

def _column_names(cursor: sqlite3.Cursor, table: str) -> List[str]:
    """Return existing column names for a table (empty list if it doesn't exist)."""
    return [row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()]


def init_db() -> None:
    """Initialize the database and migrate legacy tables to the per-user schema."""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            volume REAL,
            timestamp REAL NOT NULL
        )
        """
    )

    # Accounts.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at    TEXT DEFAULT (datetime('now'))
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            message TEXT,
            threshold REAL,
            multiplier REAL,
            zscore REAL,
            active INTEGER DEFAULT 1,
            created_at REAL NOT NULL
        )
        """
    )
    # Legacy alerts table (pre-auth) lacks user_id — add it non-destructively.
    if "user_id" not in _column_names(cursor, "alerts"):
        cursor.execute("ALTER TABLE alerts ADD COLUMN user_id INTEGER")

    # Portfolio: per-user, unique per (user, symbol). The pre-auth table had a
    # bare UNIQUE(symbol) constraint that can't be altered in place, so recreate
    # it when migrating. Existing rows are preserved with a NULL owner (they
    # simply won't surface for any signed-in user).
    portfolio_cols = _column_names(cursor, "portfolio")
    if portfolio_cols and "user_id" not in portfolio_cols:
        cursor.execute("ALTER TABLE portfolio RENAME TO portfolio_legacy")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            symbol     TEXT    NOT NULL COLLATE NOCASE,
            shares     REAL    NOT NULL CHECK (shares > 0),
            avg_cost   REAL,
            added_at   TEXT    DEFAULT (datetime('now')),
            UNIQUE(user_id, symbol)
        )
        """
    )
    if _table_exists(cursor, "portfolio_legacy"):
        cursor.execute(
            """
            INSERT INTO portfolio (user_id, symbol, shares, avg_cost, added_at)
            SELECT NULL, symbol, shares, avg_cost, added_at FROM portfolio_legacy
            """
        )
        cursor.execute("DROP TABLE portfolio_legacy")

    # Per-user persistent watchlist (previously in-memory only).
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            symbol   TEXT NOT NULL COLLATE NOCASE,
            added_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, symbol)
        )
        """
    )

    conn.commit()
    conn.close()


def _table_exists(cursor: sqlite3.Cursor, table: str) -> bool:
    row = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


# ------------------------------------------------------------------------------
# Users
# ------------------------------------------------------------------------------

def create_user(email: str, password_hash: str) -> Optional[int]:
    """Insert a new user. Returns the new id, or None if the email is taken."""
    conn = _connect()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email.strip().lower(), password_hash),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[dict]:
    conn = _connect()
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT id, email, password_hash FROM users WHERE email = ?",
        (email.strip().lower(),),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "password_hash": row[2]}


def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = _connect()
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"id": row[0], "email": row[1], "created_at": row[2]}


# ------------------------------------------------------------------------------
# Price Storage
# ------------------------------------------------------------------------------

def insert_price(symbol: str, price: float, volume: float = 0.0) -> None:
    """Insert a price row with timestamp."""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO prices (symbol, price, volume, timestamp)
        VALUES (?, ?, ?, ?)
        """,
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

    cursor.execute(
        """
        SELECT timestamp, price, volume
        FROM prices
        WHERE symbol = ?
        ORDER BY timestamp ASC
        LIMIT ?
        """,
        (symbol, limit),
    )

    rows = cursor.fetchall()
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

    cursor.execute(
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

    rows = cursor.fetchall()
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

    cursor.execute(
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

    cursor.execute(
        """
        INSERT INTO alerts (
            user_id, symbol, alert_type, threshold, multiplier, zscore, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, symbol, alert_type, threshold, multiplier, zscore, time.time()),
    )

    alert_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return alert_id


def get_recent_alerts(symbol: str, limit: int = 10):
    """
    Return recent alerts for a symbol as (timestamp, alert_type, message).
    """
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT created_at, alert_type, message
        FROM alerts
        WHERE symbol = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (symbol, limit),
    )

    rows = cursor.fetchall()
    conn.close()
    return rows


def get_alerts(user_id: int):
    """
    Return a user's active alerts (for dashboard listing).
    """
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, symbol, alert_type, threshold, multiplier, zscore, created_at
        FROM alerts
        WHERE active = 1 AND user_id = ?
        ORDER BY created_at DESC
        """,
        (user_id,),
    )

    rows = cursor.fetchall()
    conn.close()
    return rows


def delete_alert(alert_id: int, user_id: int) -> None:
    """Delete an alert by id, scoped to its owner."""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM alerts WHERE id = ? AND user_id = ?", (alert_id, user_id)
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

    cursor.execute(
        """
        INSERT INTO portfolio (user_id, symbol, shares, avg_cost, added_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, symbol) DO UPDATE SET
            shares   = excluded.shares,
            avg_cost = excluded.avg_cost,
            added_at = excluded.added_at
        """,
        (user_id, sym, shares, avg_cost),
    )

    conn.commit()
    row_id: int = cursor.execute(
        "SELECT id FROM portfolio WHERE user_id = ? AND symbol = ?", (user_id, sym)
    ).fetchone()[0]
    conn.close()
    return row_id


def get_portfolio(user_id: int) -> List[dict]:
    """Return a user's portfolio holdings ordered by symbol."""
    conn = _connect()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, symbol, shares, avg_cost, added_at
        FROM portfolio
        WHERE user_id = ?
        ORDER BY symbol ASC
        """,
        (user_id,),
    )

    rows = cursor.fetchall()
    conn.close()
    return [
        {
            "id": r[0],
            "symbol": r[1],
            "shares": r[2],
            "avg_cost": r[3],
            "added_at": r[4],
        }
        for r in rows
    ]


def delete_holding(holding_id: int, user_id: int) -> None:
    """Remove a user's portfolio holding by id (scoped to its owner)."""
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM portfolio WHERE id = ? AND user_id = ?", (holding_id, user_id)
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
    rows = cursor.execute(
        "SELECT symbol FROM watchlist WHERE user_id = ? ORDER BY added_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def add_to_watchlist(user_id: int, symbol: str) -> None:
    """Add a symbol to a user's watchlist (no-op if already present)."""
    sym = symbol.upper().strip()
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
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
    cursor.execute(
        "DELETE FROM watchlist WHERE user_id = ? AND symbol = ?", (user_id, sym)
    )
    conn.commit()
    conn.close()
