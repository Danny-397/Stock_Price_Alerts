"""Tests for authentication helpers and user-scoped persistence."""

import os

import tracker.database as db
from tracker import auth


_TEST_DB = "data/test_auth.db"


def setup_function():
    db.DB_PATH = _TEST_DB
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    db.init_db()


def teardown_function():
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)


# ── Password hashing ─────────────────────────────────────────────────────

def test_password_hash_roundtrip():
    h = auth.hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"        # never stored in clear
    assert auth.verify_password("correct horse battery staple", h)
    assert not auth.verify_password("wrong password", h)


# ── Token signing ────────────────────────────────────────────────────────

def test_token_roundtrip():
    token = auth.generate_token(42)
    assert auth.verify_token(token) == 42


def test_tampered_token_is_rejected():
    token = auth.generate_token(42)
    assert auth.verify_token(token + "x") is None
    assert auth.verify_token("not-a-token") is None
    assert auth.verify_token("") is None


def test_expired_token_is_rejected():
    token = auth.generate_token(42)
    # A negative max_age places the expiry before issuance, so any token is stale.
    assert auth.verify_token(token, max_age=-1) is None


# ── Validation ───────────────────────────────────────────────────────────

def test_email_validation():
    assert auth.is_valid_email("a@b.com")
    assert not auth.is_valid_email("nope")
    assert not auth.is_valid_email("a@b")
    assert not auth.is_valid_email("")


def test_password_policy():
    assert auth.password_problem("short") is not None
    assert auth.password_problem("longenough") is None


# ── User persistence ─────────────────────────────────────────────────────

def test_create_and_fetch_user():
    uid = db.create_user("Person@Example.com", auth.hash_password("password123"))
    assert uid is not None
    fetched = db.get_user_by_email("person@example.com")   # case-insensitive
    assert fetched["id"] == uid
    assert auth.verify_password("password123", fetched["password_hash"])


def test_duplicate_email_rejected():
    assert db.create_user("dupe@example.com", "h") is not None
    assert db.create_user("dupe@example.com", "h2") is None


# ── Alert + watchlist scoping ────────────────────────────────────────────

def test_alerts_are_user_scoped():
    u1 = db.create_user("u1@example.com", "h")
    u2 = db.create_user("u2@example.com", "h")
    db.create_alert("AAPL", "price_above", threshold=200, user_id=u1)
    assert len(db.get_alerts(u1)) == 1
    assert len(db.get_alerts(u2)) == 0


def test_watchlist_roundtrip_and_dedupe():
    uid = db.create_user("w@example.com", "h")
    db.add_to_watchlist(uid, "AAPL")
    db.add_to_watchlist(uid, "aapl")   # duplicate (case-insensitive) — ignored
    db.add_to_watchlist(uid, "MSFT")
    assert set(db.get_watchlist(uid)) == {"AAPL", "MSFT"}
    db.remove_from_watchlist(uid, "AAPL")
    assert db.get_watchlist(uid) == ["MSFT"]


# ── Legacy schema migration ──────────────────────────────────────────────

def test_legacy_portfolio_migrates_to_per_user():
    """A pre-auth portfolio table (UNIQUE(symbol), no user_id) must migrate
    non-destructively and allow two users to hold the same symbol afterwards."""
    import sqlite3

    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    os.makedirs("data", exist_ok=True)

    # Build the old-style table by hand and seed a row.
    conn = sqlite3.connect(_TEST_DB)
    conn.execute(
        """
        CREATE TABLE portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE COLLATE NOCASE,
            shares REAL NOT NULL CHECK (shares > 0),
            avg_cost REAL,
            added_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("INSERT INTO portfolio (symbol, shares, avg_cost) VALUES ('AAPL', 5, 100)")
    conn.commit()
    conn.close()

    db.DB_PATH = _TEST_DB
    db.init_db()   # should migrate in place, no exception

    # New schema has user_id, and the legacy row is preserved (owner NULL).
    conn = sqlite3.connect(_TEST_DB)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(portfolio)").fetchall()]
    assert "user_id" in cols
    total = conn.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]
    assert total == 1
    conn.close()

    # Two real users can now both hold AAPL — the old UNIQUE(symbol) is gone.
    u1 = db.create_user("m1@example.com", "h")
    u2 = db.create_user("m2@example.com", "h")
    db.upsert_holding(u1, "AAPL", 10)
    db.upsert_holding(u2, "AAPL", 20)
    assert db.get_portfolio(u1)[0]["shares"] == 10
    assert db.get_portfolio(u2)[0]["shares"] == 20
