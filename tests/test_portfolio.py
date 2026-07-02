"""Tests for per-user portfolio CRUD operations in tracker/database.py."""

import os

import tracker.database as db


_TEST_DB = "data/test_portfolio.db"
_UID = 1        # primary test user
_OTHER = 2      # second user, for isolation checks


def setup_function():
    db.DB_PATH = _TEST_DB
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)
    db.init_db()
    # Real users so foreign-key-style scoping is realistic.
    db.create_user("owner@example.com", "x")
    db.create_user("other@example.com", "x")


def teardown_function():
    if os.path.exists(_TEST_DB):
        os.remove(_TEST_DB)


def test_add_holding_and_fetch():
    db.upsert_holding(_UID, "AAPL", 10.0, avg_cost=150.0)
    holdings = db.get_portfolio(_UID)
    assert len(holdings) == 1
    assert holdings[0]["symbol"] == "AAPL"
    assert holdings[0]["shares"] == 10.0
    assert holdings[0]["avg_cost"] == 150.0


def test_upsert_updates_existing_symbol():
    db.upsert_holding(_UID, "AAPL", 10.0, avg_cost=150.0)
    db.upsert_holding(_UID, "AAPL", 15.0, avg_cost=160.0)  # update same symbol
    holdings = db.get_portfolio(_UID)
    assert len(holdings) == 1
    assert holdings[0]["shares"] == 15.0
    assert holdings[0]["avg_cost"] == 160.0


def test_multiple_different_symbols():
    db.upsert_holding(_UID, "AAPL", 10.0)
    db.upsert_holding(_UID, "MSFT", 5.0, avg_cost=300.0)
    db.upsert_holding(_UID, "NVDA", 2.5)
    holdings = db.get_portfolio(_UID)
    assert len(holdings) == 3
    symbols = [h["symbol"] for h in holdings]
    assert "AAPL" in symbols
    assert "MSFT" in symbols
    assert "NVDA" in symbols


def test_delete_holding():
    hid = db.upsert_holding(_UID, "TSLA", 3.0, avg_cost=200.0)
    assert len(db.get_portfolio(_UID)) == 1
    db.delete_holding(hid, _UID)
    assert len(db.get_portfolio(_UID)) == 0


def test_holding_without_avg_cost():
    db.upsert_holding(_UID, "AMZN", 4.0)
    holdings = db.get_portfolio(_UID)
    assert holdings[0]["avg_cost"] is None


def test_symbol_case_insensitive():
    db.upsert_holding(_UID, "aapl", 10.0)
    db.upsert_holding(_UID, "AAPL", 20.0)   # same symbol, different case → upsert
    holdings = db.get_portfolio(_UID)
    assert len(holdings) == 1
    assert holdings[0]["shares"] == 20.0


def test_get_portfolio_returns_empty_list():
    assert db.get_portfolio(_UID) == []


# ── Per-user isolation ───────────────────────────────────────────────────

def test_users_have_independent_portfolios():
    db.upsert_holding(_UID, "AAPL", 10.0)
    db.upsert_holding(_OTHER, "AAPL", 99.0)  # same symbol, different owner
    mine = db.get_portfolio(_UID)
    theirs = db.get_portfolio(_OTHER)
    assert len(mine) == 1 and mine[0]["shares"] == 10.0
    assert len(theirs) == 1 and theirs[0]["shares"] == 99.0


def test_cannot_delete_another_users_holding():
    hid = db.upsert_holding(_OTHER, "TSLA", 3.0)
    db.delete_holding(hid, _UID)              # wrong owner — must be a no-op
    assert len(db.get_portfolio(_OTHER)) == 1
