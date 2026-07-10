"""Plotly dashboard backend using Flask + Socket.IO for Tradeski."""

import sys
import os

# When run via gunicorn --chdir, the repo root is the parent of this file's directory.
# Insert it so `tracker` can always be found regardless of working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import re
import time
import datetime
from functools import wraps
from typing import List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from flask_socketio import SocketIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import anthropic

from cache import SimpleCache
from tracker import database
from tracker import auth
from tracker.fred import get_macro_snapshot, format_macro_context
from tracker.news import fetch_news, aggregate_sentiment, format_news_context
from tracker.price_fetcher import get_stock_price, get_ohlc_history, get_screener_data
from tracker.analyzer import (
    sma,
    ema,
    rsi,
    macd,
    bollinger_bands,
    zscore,
    volatility,
    linear_regression_prediction,
)

app = Flask(__name__)

_raw_origins = os.environ.get("ALLOWED_ORIGINS", "https://tradeski.dev,https://www.tradeski.dev")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()] if _raw_origins != "*" else "*"

CORS(app, origins=_allowed_origins)

socketio = SocketIO(
    app,
    cors_allowed_origins=_allowed_origins,
    async_mode="gevent",
)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["10 per minute"],
    storage_uri="memory://",
)

_cache = SimpleCache()
_FRED_TTL     = 3600   # refresh FRED data at most once per hour
_CHART_TTL    = 300    # price history / stats cache: 5 minutes
_NEWS_TTL     = 1800   # news + sentiment cache: 30 minutes
_SCREENER_TTL = 600    # screener fundamentals cache: 10 minutes
_CORR_TTL     = 3600  # correlation matrix cache: 1 hour

_CORR_SYMS = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "SOFI", "RDW", "GOOGL", "META", "SPY", "QQQ"]

_SCREENER_UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AMD", "INTC", "ORCL",
    # Financials
    "JPM", "BAC", "GS", "V", "MA",
    # Healthcare
    "JNJ", "UNH", "LLY", "ABBV", "MRK",
    # Energy
    "XOM", "CVX", "COP",
    # Consumer
    "WMT", "COST", "HD", "MCD",
    # Broad-market ETFs
    "SPY", "QQQ",
]

# Timeframe → (yfinance period, interval)
_TF_MAP: dict[str, tuple[str, str]] = {
    "1D":  ("1d",  "5m"),
    "5D":  ("5d",  "15m"),
    "1M":  ("1mo", "1d"),
    "3M":  ("3mo", "1d"),
    "6M":  ("6mo", "1d"),
    "1Y":  ("1y",  "1d"),
}


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def rows_to_dict(
    rows: List[Tuple[str, float, float]],
    limit: Optional[int],
) -> Tuple[List[str], List[float], List[float]]:
    """Convert (timestamp, price, volume) rows into labeled lists."""
    if limit:
        rows = rows[-limit:]

    timestamps = [row[0] for row in rows]
    prices = [row[1] for row in rows]
    volume = [row[2] for row in rows]
    return timestamps, prices, volume


# ─────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────

def _bearer_token() -> str:
    """Extract the bearer token from the Authorization header."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return ""


def _resolve_user() -> Optional[int]:
    """Return the authenticated user id for this request, or None."""
    return auth.verify_token(_bearer_token())


def require_auth(fn):
    """Reject the request with 401 unless it carries a valid token."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user_id = _resolve_user()
        if user_id is None:
            return jsonify({"error": "Sign in to use this feature."}), 401
        g.user_id = user_id
        return fn(*args, **kwargs)
    return wrapper


def optional_auth(fn):
    """Set g.user_id if a valid token is present; otherwise leave it None."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        g.user_id = _resolve_user()
        return fn(*args, **kwargs)
    return wrapper


@app.route("/auth/register", methods=["POST"])
@limiter.limit("10 per hour")
def register() -> tuple:
    data = request.json or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    if not auth.is_valid_email(email):
        return jsonify({"error": "Enter a valid email address."}), 400
    problem = auth.password_problem(password)
    if problem:
        return jsonify({"error": problem}), 400

    user_id = database.create_user(email, auth.hash_password(password))
    if user_id is None:
        return jsonify({"error": "An account with that email already exists."}), 409

    token = auth.generate_token(user_id)
    return jsonify({"token": token, "user": {"id": user_id, "email": email.lower()}})


@app.route("/auth/login", methods=["POST"])
@limiter.limit("10 per minute")
def login() -> tuple:
    data = request.json or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    user = database.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password_hash"]):
        return jsonify({"error": "Incorrect email or password."}), 401

    token = auth.generate_token(user["id"])
    return jsonify({"token": token, "user": {"id": user["id"], "email": user["email"]}})


@app.route("/auth/me")
@require_auth
def me() -> tuple:
    user = database.get_user_by_id(g.user_id)
    if not user:
        return jsonify({"error": "Account not found."}), 404
    return jsonify({"user": user})


# ─────────────────────────────────────────────────────────────
# REST Endpoints
# ─────────────────────────────────────────────────────────────

@app.route("/health")
def health() -> tuple:
    return jsonify({
        "status": "ok",
        "db": database.db_backend(),
        "services": {
            "fred":      "configured" if os.environ.get("FRED_API_KEY")      else "missing",
            "news":      "configured" if os.environ.get("NEWS_API_KEY")       else "missing",
            "anthropic": "configured" if os.environ.get("ANTHROPIC_API_KEY")  else "missing",
        },
    })


@app.route("/stats")
def stats() -> tuple:
    """Return OHLC + real 52-week range for a symbol (yfinance, cached 5 min)."""
    symbol = request.args.get("symbol", "AAPL").upper()
    cache_key = f"stats:{symbol}"

    cached = _cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    bars = get_ohlc_history(symbol, "1y", "1d")
    if not bars:
        return jsonify({"error": "No data"}), 404

    latest     = bars[-1]
    all_highs  = [b["high"] for b in bars]
    all_lows   = [b["low"]  for b in bars]
    change_pct = (
        (latest["close"] - latest["open"]) / latest["open"] * 100
        if latest["open"] else 0.0
    )

    result = {
        "symbol":     symbol,
        "open":       round(latest["open"],  4),
        "high":       round(latest["high"],  4),
        "low":        round(latest["low"],   4),
        "close":      round(latest["close"], 4),
        "high_52w":   round(max(all_highs),  4),
        "low_52w":    round(min(all_lows),   4),
        "change_pct": round(change_pct,      4),
    }

    _cache.set(cache_key, result, ttl=_CHART_TTL)
    return jsonify(result)


@app.route("/price_history")
def price_history() -> tuple:
    """Return real OHLC history + computed indicators (yfinance, cached 5 min)."""
    symbol = request.args.get("symbol", "AAPL").upper()
    # Accept either ?tf=1M (new) or ?limit=300 (legacy — map to nearest tf)
    tf = (request.args.get("tf") or request.args.get("timeframe") or "1M").upper()
    if tf not in _TF_MAP:
        tf = "1M"

    cache_key = f"ph:{symbol}:{tf}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    period, interval = _TF_MAP[tf]
    bars = get_ohlc_history(symbol, period, interval)
    if not bars:
        return jsonify({"error": "No data"}), 404

    timestamps = [b["timestamp"] for b in bars]
    opens      = [b["open"]      for b in bars]
    highs      = [b["high"]      for b in bars]
    lows       = [b["low"]       for b in bars]
    closes     = [b["close"]     for b in bars]
    volume     = [b["volume"]    for b in bars]

    sma20_vals             = sma(closes, 20)
    sma50_vals             = sma(closes, 50)
    ema20_vals             = ema(closes, 20)
    rsi_vals               = rsi(closes, 14)
    macd_line, sig, hist_v = macd(closes)
    upper_band, lower_band = bollinger_bands(closes, 20)
    z_vals                 = zscore(closes, 20)
    vol_vals               = volatility(closes, 20)
    prediction             = linear_regression_prediction(closes)

    result = {
        "timestamps": timestamps,
        "open":       opens,
        "high":       highs,
        "low":        lows,
        "close":      closes,
        "prices":     closes,     # keep for any legacy callers
        "volume":     volume,
        "sma20":      sma20_vals,
        "sma50":      sma50_vals,
        "ema20":      ema20_vals,
        "rsi":        rsi_vals,
        "macd":       macd_line,
        "signal":     sig,
        "histogram":  hist_v,
        "upper_band": upper_band,
        "lower_band": lower_band,
        "zscore":     z_vals,
        "volatility": vol_vals,
        "prediction": prediction,
    }

    _cache.set(cache_key, result, ttl=_CHART_TTL)
    return jsonify(result)


@app.route("/sparkline")
def sparkline() -> tuple:
    """Return 30-day close prices for a symbol (used by watchlist sparklines)."""
    symbol = request.args.get("symbol", "AAPL").upper()
    cache_key = f"sparkline:{symbol}"

    cached = _cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    bars = get_ohlc_history(symbol, "1mo", "1d")
    result = {
        "timestamps": [b["timestamp"] for b in bars],
        "close":      [b["close"]     for b in bars],
    }
    _cache.set(cache_key, result, ttl=_CHART_TTL)
    return jsonify(result)


@app.route("/watchlist")
def watchlist() -> tuple:
    """Return latest price + pct_change for a comma‑separated list of symbols."""
    raw = request.args.get("symbols", "AAPL,MSFT,TSLA,NVDA,AMZN")
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]

    result = {}

    for sym in symbols:
        rows = database.get_recent_prices(sym, limit=2)
        if not rows:
            result[sym] = None
            continue

        prices = [row[1] for row in rows]
        open_price = prices[0]
        close_price = prices[-1]

        pct_change = (
            (close_price - open_price) / open_price * 100
            if open_price else 0.0
        )

        result[sym] = {
            "price": round(close_price, 4),
            "change_pct": round(pct_change, 4),
        }

    return jsonify(result)


@app.route("/watchlist/saved", methods=["GET"])
@require_auth
def get_saved_watchlist() -> tuple:
    """Return the signed-in user's persistent watchlist symbols."""
    return jsonify({"symbols": database.get_watchlist(g.user_id)})


@app.route("/watchlist/saved", methods=["POST"])
@require_auth
def add_saved_watchlist() -> tuple:
    """Add a symbol to the signed-in user's persistent watchlist."""
    data = request.json or {}
    symbol = re.sub(r"[^A-Z0-9.]", "", (data.get("symbol") or "").upper())[:10]
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400
    database.add_to_watchlist(g.user_id, symbol)
    return jsonify({"status": "ok", "symbols": database.get_watchlist(g.user_id)})


@app.route("/watchlist/saved/<symbol>", methods=["DELETE"])
@require_auth
def delete_saved_watchlist(symbol: str) -> tuple:
    """Remove a symbol from the signed-in user's persistent watchlist."""
    clean = re.sub(r"[^A-Z0-9.]", "", (symbol or "").upper())[:10]
    database.remove_from_watchlist(g.user_id, clean)
    return jsonify({"status": "deleted", "symbols": database.get_watchlist(g.user_id)})


@app.route("/market_status")
def market_status() -> tuple:
    """Return simple market‑open heuristic based on server UTC time."""
    now = datetime.datetime.utcnow()
    day = now.weekday()
    hour = now.hour + now.minute / 60

    if day >= 5:
        status = "closed"
    elif 13.5 <= hour < 20:
        status = "open"
    elif 8 <= hour < 13.5 or 20 <= hour < 24:
        status = "extended"
    else:
        status = "closed"

    return jsonify({"status": status, "utc": now.isoformat()})


@app.route("/alerts", methods=["GET"])
@require_auth
def get_alerts() -> tuple:
    """Return the signed-in user's alerts."""
    return jsonify(database.get_alerts(g.user_id))


@app.route("/alerts", methods=["POST"])
@require_auth
def create_alert() -> tuple:
    """Create a new alert owned by the signed-in user."""
    data = request.json or {}

    symbol = data.get("symbol")
    alert_type = data.get("alert_type")
    threshold = data.get("threshold")
    multiplier = data.get("multiplier")
    zscore_val = data.get("zscore")

    alert_id = database.create_alert(
        symbol,
        alert_type,
        threshold,
        multiplier,
        zscore_val,
        user_id=g.user_id,
    )

    return jsonify({"status": "ok", "alert_id": alert_id})


@app.route("/alerts/<int:alert_id>", methods=["DELETE"])
@require_auth
def delete_alert(alert_id: int) -> tuple:
    """Delete one of the signed-in user's alerts."""
    database.delete_alert(alert_id, g.user_id)
    return jsonify({"status": "deleted"})


@app.route("/macro")
def macro() -> tuple:
    """Return latest FRED macro indicator snapshot (cached 1 h)."""
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        return jsonify({"error": "FRED_API_KEY not configured — add it in the Render dashboard Environment tab"}), 503

    cached = _cache.get("macro_snapshot")
    if cached is not None:
        return jsonify(cached)

    snapshot = get_macro_snapshot(fred_key)

    # If every series returned None the key is almost certainly invalid
    if all(v.get("value") is None for v in snapshot.values()):
        return jsonify({"error": "FRED returned no data — API key is likely invalid or FRED is down. Get a free key at fred.stlouisfed.org/docs/api/api_key.html"}), 502

    _cache.set("macro_snapshot", snapshot, ttl=_FRED_TTL)
    return jsonify(snapshot)


def _get_macro_context() -> str:
    """Return a formatted macro context string for Ski, using the cache."""
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        return ""
    cached = _cache.get("macro_snapshot")
    if cached is None:
        cached = get_macro_snapshot(fred_key)
        _cache.set("macro_snapshot", cached, ttl=_FRED_TTL)
    return format_macro_context(cached)


# ─────────────────────────────────────────────────────────────
# Portfolio Endpoints
# ─────────────────────────────────────────────────────────────

def _enrich_holding(h: dict) -> dict:
    """Add current price, market value, and P&L to a raw holding dict."""
    symbol = h["symbol"]
    shares = h["shares"]
    avg_cost = h.get("avg_cost")

    rows = database.get_recent_prices(symbol, limit=1)
    if rows:
        current_price = rows[0][1]
    else:
        current_price = get_stock_price(symbol)

    market_value = round(shares * current_price, 2) if current_price else None

    pnl_pct = None
    pnl_abs = None
    if current_price and avg_cost:
        pnl_pct = round((current_price - avg_cost) / avg_cost * 100, 2)
        pnl_abs = round((current_price - avg_cost) * shares, 2)

    return {
        **h,
        "current_price": round(current_price, 4) if current_price else None,
        "market_value": market_value,
        "pnl_pct": pnl_pct,
        "pnl_abs": pnl_abs,
    }


@app.route("/portfolio", methods=["GET"])
@require_auth
def get_portfolio_route() -> tuple:
    """Return the signed-in user's holdings enriched with current prices and P&L."""
    holdings = [_enrich_holding(h) for h in database.get_portfolio(g.user_id)]

    total_value = sum(h["market_value"] for h in holdings if h["market_value"])
    cost_basis = sum(
        h["shares"] * h["avg_cost"]
        for h in holdings
        if h["avg_cost"]
    )
    total_pnl_pct = (
        round((total_value - cost_basis) / cost_basis * 100, 2)
        if cost_basis else None
    )
    total_pnl_abs = round(total_value - cost_basis, 2) if cost_basis else None

    return jsonify({
        "holdings": holdings,
        "total_value": round(total_value, 2),
        "total_pnl_pct": total_pnl_pct,
        "total_pnl_abs": total_pnl_abs,
    })


@app.route("/portfolio", methods=["POST"])
@require_auth
def add_holding_route() -> tuple:
    """Add or update one of the signed-in user's holdings."""
    data = request.json or {}
    symbol = (data.get("symbol") or "").strip().upper()
    shares = data.get("shares")
    avg_cost = data.get("avg_cost")

    if not symbol:
        return jsonify({"error": "symbol is required"}), 400
    try:
        shares = float(shares)
        if shares <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "shares must be a positive number"}), 400
    if avg_cost is not None:
        try:
            avg_cost = float(avg_cost)
            if avg_cost < 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "avg_cost must be a non-negative number"}), 400

    holding_id = database.upsert_holding(g.user_id, symbol, shares, avg_cost)
    return jsonify({"status": "ok", "id": holding_id})


@app.route("/portfolio/<int:holding_id>", methods=["DELETE"])
@require_auth
def delete_holding_route(holding_id: int) -> tuple:
    """Remove one of the signed-in user's holdings."""
    database.delete_holding(holding_id, g.user_id)
    return jsonify({"status": "deleted"})


def _get_portfolio_context(user_id: Optional[int]) -> str:
    """Format a user's portfolio for injection into Ski's context.

    Beyond raw holdings, this surfaces each position's weight (concentration),
    the portfolio's computed risk metrics (Sharpe, beta, volatility), and the
    user's watchlist — so Ski can reason about diversification and risk, not
    just list positions.
    """
    if user_id is None:
        return ""
    holdings = [_enrich_holding(h) for h in database.get_portfolio(user_id)]
    if not holdings:
        return ""

    total_value = sum(h["market_value"] for h in holdings if h.get("market_value"))
    total_cost = sum(
        h["shares"] * h["avg_cost"] for h in holdings if h.get("avg_cost")
    )

    lines = [f"USER'S PORTFOLIO — {datetime.date.today()}:"]

    # Largest positions first so Ski leads with what matters most.
    ordered = sorted(
        holdings, key=lambda h: h.get("market_value") or 0, reverse=True
    )
    for h in ordered:
        symbol = h["symbol"]
        shares = h["shares"]
        avg_cost = h.get("avg_cost")
        current = h.get("current_price")
        mv = h.get("market_value")

        line = f"  {symbol}: {shares} shares"
        if avg_cost:
            line += f" @ ${avg_cost:.2f} avg cost"
        if current:
            line += f" | price ${current:.2f}"
        if mv:
            line += f" | value ${mv:,.2f}"
            if total_value:
                line += f" | {mv / total_value * 100:.0f}% of portfolio"
        if h.get("pnl_pct") is not None:
            sign = "+" if h["pnl_pct"] >= 0 else ""
            line += f" | P&L {sign}{h['pnl_pct']:.1f}% (${h['pnl_abs']:+,.2f})"
        lines.append(line)

    if total_value:
        lines.append(f"  TOTAL VALUE: ${total_value:,.2f}")
    if total_cost and total_value:
        pnl = (total_value - total_cost) / total_cost * 100
        sign = "+" if pnl >= 0 else ""
        lines.append(f"  TOTAL P&L: {sign}{pnl:.1f}%")

    # Concentration note — the single biggest position by weight.
    if total_value and len(ordered) > 1 and ordered[0].get("market_value"):
        top = ordered[0]
        top_wt = top["market_value"] / total_value * 100
        if top_wt >= 40:
            lines.append(
                f"  CONCENTRATION: {top['symbol']} is {top_wt:.0f}% of the portfolio "
                f"— flag single-name concentration risk when relevant."
            )

    risk = _portfolio_risk_cached(user_id)
    if risk:
        lines.append(
            "  RISK METRICS (1Y daily returns, value-weighted): "
            f"Sharpe {risk['sharpe']}, "
            f"beta vs S&P 500 {risk['beta']}, "
            f"annualized volatility {risk['volatility']}%."
        )

    watch = database.get_watchlist(user_id)
    if watch:
        lines.append(f"  WATCHLIST (not held): {', '.join(watch[:15])}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# News + Sentiment Endpoints
# ─────────────────────────────────────────────────────────────

def _fetch_cached_news(symbol: str) -> list:
    """Return cached article list for symbol, fetching if stale."""
    news_key = os.environ.get("NEWS_API_KEY", "")
    if not news_key:
        return []
    cache_key = f"news:{symbol}"
    articles = _cache.get(cache_key)
    if articles is None:
        articles = fetch_news(symbol, news_key)
        _cache.set(cache_key, articles, ttl=_NEWS_TTL)
    return articles


@app.route("/news")
def news_endpoint() -> tuple:
    """Return scored headlines + aggregate sentiment for a symbol."""
    symbol   = request.args.get("symbol", "AAPL").upper()
    news_key = os.environ.get("NEWS_API_KEY", "")

    if not news_key:
        return jsonify({"error": "NEWS_API_KEY not configured"}), 503

    articles = _fetch_cached_news(symbol)
    agg      = aggregate_sentiment(articles)

    return jsonify({
        "symbol":    symbol,
        "articles":  articles,
        "aggregate": agg,
    })


def _get_news_context(symbol: str) -> str:
    """Format recent news sentiment as a context string for Ski."""
    if not symbol:
        return ""
    articles = _fetch_cached_news(symbol)
    if not articles:
        return ""
    return format_news_context(symbol, articles, aggregate_sentiment(articles))


def _format_chart_context(ctx: dict) -> str:
    """Format the user's current chart indicator snapshot for Ski."""
    symbol = ctx.get("symbol", "")
    tf = ctx.get("timeframe", "")
    lines = [f"CURRENT CHART — {symbol} ({tf} timeframe):"]

    close = ctx.get("close")
    if close is not None:
        lines.append(f"  Latest close: ${close:.2f}")

    rsi = ctx.get("rsi")
    if rsi is not None:
        sig = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
        lines.append(f"  RSI (14): {rsi:.1f} — {sig}")

    macd = ctx.get("macd")
    signal = ctx.get("macd_signal")
    hist = ctx.get("macd_histogram")
    if macd is not None and signal is not None:
        direction = "bullish crossover" if hist and hist > 0 else "bearish crossover"
        lines.append(f"  MACD: {macd:.3f}, Signal: {signal:.3f}, Histogram: {hist:+.3f} ({direction})")

    sma20 = ctx.get("sma20")
    sma50 = ctx.get("sma50")
    ema20 = ctx.get("ema20")
    if sma20 is not None:
        lines.append(f"  SMA 20: ${sma20:.2f}")
    if sma50 is not None:
        lines.append(f"  SMA 50: ${sma50:.2f}")
    if ema20 is not None:
        lines.append(f"  EMA 20: ${ema20:.2f}")

    upper = ctx.get("upper_band")
    lower = ctx.get("lower_band")
    if upper is not None and lower is not None and sma20 is not None:
        lines.append(f"  Bollinger Bands: Upper ${upper:.2f} / Mid ${sma20:.2f} / Lower ${lower:.2f}")

    zscore = ctx.get("zscore")
    if zscore is not None:
        lines.append(f"  Z-Score: {zscore:.2f}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Screener Endpoint
# ─────────────────────────────────────────────────────────────

def _fetch_screener_row(symbol: str) -> dict | None:
    """Return cached screener row for symbol, fetching from yfinance if stale."""
    cache_key = f"screener:{symbol}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached
    row = get_screener_data(symbol)
    if row:
        _cache.set(cache_key, row, ttl=_SCREENER_TTL)
    return row


@app.route("/screener")
def screener() -> tuple:
    """Return fundamental data for the built-in stock universe (parallel fetch, cached)."""
    stocks: list = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_screener_row, sym): sym for sym in _SCREENER_UNIVERSE}
        for future in as_completed(futures):
            row = future.result()
            if row:
                stocks.append(row)
    stocks.sort(key=lambda r: r["symbol"])
    return jsonify({"stocks": stocks, "universe_size": len(_SCREENER_UNIVERSE)})


# ─────────────────────────────────────────────────────────────
# Correlation Heatmap
# ─────────────────────────────────────────────────────────────

@app.route("/correlation")
def correlation_endpoint() -> tuple:
    """Return 90-day return correlation matrix for tracked symbols."""
    cached = _cache.get("correlation")
    if cached:
        return jsonify(cached)

    closes: dict = {}
    for sym in _CORR_SYMS:
        bars = get_ohlc_history(sym, "3mo", "1d")
        if bars and len(bars) > 15:
            closes[sym] = [b["close"] for b in bars]

    if len(closes) < 2:
        return jsonify({"error": "Insufficient data"}), 503

    min_len = min(len(v) for v in closes.values())
    syms = list(closes.keys())

    returns_matrix = []
    for sym in syms:
        prices = np.array(closes[sym][-min_len:], dtype=float)
        returns_matrix.append(np.diff(prices) / prices[:-1])

    corr = np.corrcoef(returns_matrix)
    result = {
        "symbols": syms,
        "matrix": [[round(float(corr[i][j]), 3) for j in range(len(syms))] for i in range(len(syms))],
    }
    _cache.set("correlation", result, ttl=_CORR_TTL)
    return jsonify(result)


# ─────────────────────────────────────────────────────────────
# Portfolio Risk Metrics
# ─────────────────────────────────────────────────────────────

def _compute_portfolio_risk(holdings: List[dict]) -> Tuple[Optional[dict], Optional[str]]:
    """Compute Sharpe, beta vs SPY, and annualized vol from enriched holdings.

    Returns (metrics, None) on success or (None, reason) when it can't be computed.
    """
    valued = [h for h in holdings if h.get("market_value") and h["market_value"] > 0]
    if not valued:
        return None, "No valued holdings"

    total_value = sum(h["market_value"] for h in valued)
    fetch_syms = list({h["symbol"] for h in valued}) + ["SPY"]

    price_data: dict = {}
    for sym in fetch_syms:
        bars = get_ohlc_history(sym, "1y", "1d")
        if bars and len(bars) > 20:
            prices = np.array([b["close"] for b in bars], dtype=float)
            price_data[sym] = np.diff(prices) / prices[:-1]

    if "SPY" not in price_data:
        return None, "Insufficient data"

    min_len = min(len(v) for v in price_data.values())
    aligned = {k: v[-min_len:] for k, v in price_data.items()}

    port_returns = np.zeros(min_len)
    for h in valued:
        sym = h["symbol"]
        if sym in aligned:
            port_returns += (h["market_value"] / total_value) * aligned[sym]

    spy_returns = aligned["SPY"]
    std = float(np.std(port_returns))
    if std == 0:
        return None, "Insufficient variance"

    RISK_FREE_DAILY = 0.045 / 252
    ann_vol = round(std * float(np.sqrt(252)) * 100, 1)
    sharpe = round(float((np.mean(port_returns) - RISK_FREE_DAILY) / std * np.sqrt(252)), 2)
    spy_var = float(np.var(spy_returns))
    beta = round(float(np.cov(port_returns, spy_returns)[0][1] / spy_var), 2) if spy_var > 0 else 1.0

    return {"sharpe": sharpe, "beta": beta, "volatility": ann_vol}, None


def _portfolio_risk_cached(user_id: int) -> Optional[dict]:
    """Return cached portfolio risk metrics for a user, computing them if stale."""
    key = f"risk:{user_id}"
    cached = _cache.get(key)
    if cached is not None:
        return cached
    holdings = [_enrich_holding(h) for h in database.get_portfolio(user_id)]
    result, _err = _compute_portfolio_risk(holdings)
    if result is not None:
        _cache.set(key, result, ttl=300)
    return result


@app.route("/portfolio/risk")
@require_auth
def portfolio_risk_endpoint() -> tuple:
    """Return Sharpe ratio, beta vs SPY, and annualized volatility for the user's portfolio."""
    holdings = [_enrich_holding(h) for h in database.get_portfolio(g.user_id)]
    result, err = _compute_portfolio_risk(holdings)
    if result is None:
        return jsonify({"error": err}), 404 if err == "No valued holdings" else 503
    _cache.set(f"risk:{g.user_id}", result, ttl=300)
    return jsonify(result)


_SKI_SYSTEM_PROMPT = """You are Ski, a financial Q&A assistant built into the Tradeski real-time market analytics platform. \
You are sharp, concise, and authoritative — like a seasoned Wall Street analyst who can explain complex concepts clearly \
to retail traders.

Your areas of expertise:

CORE FINANCIAL CONCEPTS
- Equities: stocks, ETFs, IPOs, stock splits, dividends, buybacks, short selling, margin trading
- Fixed income: bonds, yield curves, duration, credit spreads, Treasury rates
- Derivatives: options (calls/puts, Greeks, IV, covered calls), futures, hedging strategies
- Market structure: order types, bid-ask spread, market makers, dark pools, circuit breakers
- Valuation: P/E, P/S, EV/EBITDA, DCF, book value, earnings per share, revenue growth

MACROECONOMIC TRENDS AND MARKET CONSEQUENCES
Federal Reserve policy:
  - Rate hikes → higher borrowing costs → pressure on growth/tech stocks (higher discount rate lowers DCF valuations); \
banking sector spreads widen (net interest margin expands, benefiting bank stocks); housing/mortgage-sensitive names fall
  - Rate cuts → growth/tech stocks re-rate higher; financials compress; bond prices rise; \
REITs and dividend stocks become more attractive; USD typically weakens
  - Quantitative tightening (QT) → liquidity withdrawal → risk assets sell off
  - Yield curve inversion (2s10s) → historically precedes recession by 12–18 months; watch regional bank stocks

Inflation dynamics:
  - CPI/PCE beats → hawkish Fed expectations → rates up → tech/growth down; commodities, energy, materials outperform
  - Disinflation → growth stocks re-rate; consumer discretionary recovers
  - Stagflation → defensive sectors (utilities, consumer staples, healthcare) outperform; real assets outperform

Economic growth indicators:
  - Strong GDP / PMI beats → cyclicals (industrials, materials, financials) outperform; risk-on rotation
  - Weak GDP / recession fears → defensives, Treasuries, gold rally; credit spreads widen
  - Unemployment rate: falling UE → inflationary → hawkish; rising UE → dovish pivot bets → growth rally

Currency and global macro:
  - Strong USD → headwind for multinationals (revenue translated back at worse rates); commodities priced in USD fall
  - Weak USD → tailwind for exporters; emerging markets rally; gold and commodities rise
  - China stimulus → commodity producers, luxury goods, semiconductors benefit
  - Geopolitical risk → oil spikes → energy stocks; defense contractors; safe-haven flows into gold, USD, Treasuries

SECTOR ROTATION AND MICROECONOMIC TRENDS
- Tech earnings beats (FAANG/MAG7) → sector multiple expansion; semiconductor cycle drives memory/foundry stocks
- Energy: oil supply/demand balance, OPEC cuts, inventory data (EIA weekly) drive XLE, refiners
- Healthcare: FDA approvals, drug pricing legislation, biotech binary events (trial readouts)
- Consumer: retail sales data, confidence surveys, credit card delinquency rates signal consumer health
- Real estate: mortgage rates directly impact homebuilders and REITs; cap rates vs. Treasury spreads matter
- Financials: credit quality, loan growth, net interest margin, reserve releases
- Industrials: PMI, capex cycles, reshoring trends, defense budgets
- Semiconductors: PC/smartphone/server demand cycles; AI capex buildout (NVDA, AMD, TSM)

MARKET INDICATORS TO WATCH
- VIX (volatility index): spike above 20 = fear; above 30 = panic; mean-reversion after spikes
- Put/call ratio: elevated = bearish sentiment / potential contrarian buy
- Credit spreads (HY, IG): widening = risk-off; compressing = risk-on
- AAII sentiment: extreme bearish readings historically contrarian bullish
- Options flow: unusual call sweeps = institutional bullish positioning
- Short interest: high short interest + positive catalyst = potential short squeeze

TRADESKI PLATFORM
- You have access to real-time price data, technical indicators (RSI, MACD, Bollinger Bands, SMA, EMA, ATR, Z-Score, \
Stochastic, Linear Regression), and alert functionality
- When relevant, suggest which indicators on the Tradeski dashboard are most useful for the user's question

PORTFOLIO-AWARE BEHAVIOR
You have access to the user's actual portfolio — holdings with cost basis and live P&L, each position's WEIGHT (percent \
of the portfolio), the portfolio's computed RISK METRICS (Sharpe ratio, beta vs. the S&P 500, annualized volatility), any \
CONCENTRATION flag, and their WATCHLIST — plus current macro data and news, all injected below. Use it:
- Always reference the user's specific tickers, weights, and P&L rather than generic market commentary.
- Reason about risk and diversification: cite their beta when discussing market sensitivity, their volatility/Sharpe when \
discussing risk-adjusted performance, and call out single-name concentration when one position dominates.
- Prioritize: how does today's macro environment affect THIS user's specific, largest-weighted holdings? Lead with the \
positions most affected before general context.
- Treat watchlist names as candidates the user is considering but does not yet own.
Interpretation guide: beta > 1 means more volatile than the market (amplified moves); a Sharpe below ~1 is weak \
risk-adjusted return; a single position above ~40% of the book is concentrated. Explain these in plain language.

Keep answers focused and actionable. Use specific examples. If you don't know something, say so. Never give personalized \
investment advice — always clarify you're providing educational information, not a recommendation to buy or sell."""


@app.route("/chat", methods=["POST"])
@limiter.limit("20 per hour; 50 per day")
@optional_auth
def chat() -> tuple:
    """Ski financial Q&A chatbot powered by Claude."""
    data = request.json or {}
    raw_msg = (data.get("message") or "")
    message = re.sub(r'<[^>]+>', '', raw_msg).strip()[:500]
    history = data.get("history") or []
    symbol  = re.sub(r'[^A-Z0-9.]', '', (data.get("symbol") or "").upper())[:10]
    chart_context = data.get("chart_context") or {}

    if not message:
        return jsonify({"error": "No message provided"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "AI service not configured"}), 503

    # Build system prompt
    system_parts = [_SKI_SYSTEM_PROMPT]
    now = datetime.datetime.now(datetime.timezone.utc)
    system_parts.append(
        f"CURRENT DATE: Today is {now:%A, %B %d, %Y}. "
        "Your built-in knowledge has a training cutoff and may be stale for time-sensitive facts — "
        "who currently holds offices (e.g. the Federal Reserve Chair), the latest Fed rate decisions, "
        "recent economic data releases (CPI, jobs reports), earnings, or breaking news. Whenever a "
        "question depends on a current fact that could have changed since your training, USE THE "
        "web_search TOOL to verify it and answer from those fresh results instead of from memory. "
        "Briefly mention the source/date when you rely on a search."
    )
    macro_ctx = _get_macro_context()
    if macro_ctx:
        system_parts.append(macro_ctx)
    portfolio_ctx = _get_portfolio_context(g.get("user_id"))
    if portfolio_ctx:
        system_parts.append(portfolio_ctx)
    news_ctx = _get_news_context(symbol)
    if news_ctx:
        system_parts.append(news_ctx)
    if chart_context:
        system_parts.append(_format_chart_context(chart_context))

    # Build messages list — Anthropic requires strict user/assistant alternation
    messages = []
    expected_role = "user"
    for turn in history[-10:]:
        role = (turn.get("role") or "").strip()
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if role != expected_role:
            continue
        messages.append({"role": role, "content": content})
        expected_role = "assistant" if expected_role == "user" else "user"

    # Current message must go last as "user"
    if messages and messages[-1]["role"] == "user":
        messages.append({"role": "assistant", "content": "..."})
    messages.append({"role": "user", "content": message})

    try:
        client = anthropic.Anthropic(api_key=api_key)
        system_prompt = "\n\n".join(system_parts)

        def _ask(use_web_search: bool):
            kwargs = dict(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
            )
            if use_web_search:
                # Server-side web search so Ski can pull current facts
                # (Fed chair, latest data, news) instead of stale memory.
                kwargs["tools"] = [{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                }]
            return client.messages.create(**kwargs)

        try:
            response = _ask(True)
        except (anthropic.BadRequestError, anthropic.PermissionDeniedError):
            # Web search not enabled for this account/model — answer without it.
            response = _ask(False)

        # With web search the response holds several blocks (search calls +
        # results + answer); the reply is the concatenation of the text blocks.
        reply = "".join(
            getattr(b, "text", "") for b in response.content
            if getattr(b, "type", None) == "text"
        ).strip() or "Sorry, I couldn't generate a response just now — try again."
        return jsonify({"reply": reply})
    except anthropic.RateLimitError:
        return jsonify({"error": "Ski is at capacity — I've hit my hourly AI quota. Try again in a few minutes."}), 429
    except anthropic.APIStatusError as exc:
        if exc.status_code == 529:
            return jsonify({"error": "Anthropic API is overloaded right now. Try again in 30 seconds."}), 503
        return jsonify({"error": f"AI service error ({exc.status_code})"}), 502
    except Exception as exc:
        return jsonify({"error": f"AI service error: {exc}"}), 502


_SKI_BRIEFING_PROMPT = """You are Ski, the financial-intelligence assistant built into Tradeski. \
Generate a short, PROACTIVE briefing on THIS user's portfolio using ONLY the context provided below \
(their real holdings, position weights, P&L, risk metrics, live macro data, and recent news).

Format rules — follow them exactly:
- Output 3 to 4 bullets, each starting with "• " and each a single sentence.
- Most important first: lead with the biggest risk or the biggest mover in THEIR book right now.
- Every bullet must cite a specific number from the context — a position weight %, beta, Sharpe, \
annualized volatility, P&L %, or a macro figure (CPI, Fed funds rate, unemployment). No vague filler.
- After the bullets, add one final line starting with "Watch: " naming the single most relevant thing \
to keep an eye on next (an upcoming data release, an earnings date, or a technical level).
- No greeting, no preamble, no headers, no sign-off — the bullets and the Watch line only.
- Educational only. Never phrase anything as a recommendation to buy or sell."""


@app.route("/briefing", methods=["POST"])
@limiter.limit("30 per hour; 100 per day")
@require_auth
def briefing() -> tuple:
    """Proactive, portfolio-grounded briefing Ski generates for the dashboard.

    Unlike /chat (reactive Q&A), this needs no user question: it pulls the same
    live context — macro snapshot, the user's holdings + risk metrics, and news
    for their largest position — and asks Ski to surface what matters right now.
    Result is cached per user (15 min) so dashboard loads don't burn AI quota.
    """
    user_id = g.user_id

    holdings = database.get_portfolio(user_id)
    if not holdings:
        return jsonify({"briefing": None, "reason": "no_holdings"})

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "AI service not configured"}), 503

    cache_key = f"briefing:{user_id}"
    if request.args.get("refresh") != "1":
        cached = _cache.get(cache_key)
        if cached:
            return jsonify({"briefing": cached, "cached": True})

    # Same grounded context Ski uses in chat: macro, portfolio + risk, and news
    # for the single largest holding (where headline risk hits hardest).
    system_parts = [_SKI_BRIEFING_PROMPT]
    now = datetime.datetime.now(datetime.timezone.utc)
    system_parts.append(f"CURRENT DATE: Today is {now:%A, %B %d, %Y}.")

    macro_ctx = _get_macro_context()
    if macro_ctx:
        system_parts.append(macro_ctx)

    portfolio_ctx = _get_portfolio_context(user_id)
    if portfolio_ctx:
        system_parts.append(portfolio_ctx)

    enriched = [_enrich_holding(h) for h in holdings]
    enriched.sort(key=lambda h: h.get("market_value") or 0, reverse=True)
    top_symbol = enriched[0]["symbol"] if enriched else ""
    news_ctx = _get_news_context(top_symbol) if top_symbol else ""
    if news_ctx:
        system_parts.append(news_ctx)

    system_prompt = "\n\n".join(system_parts)
    messages = [{"role": "user", "content": "Give me today's briefing on my portfolio."}]

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=system_prompt,
            messages=messages,
        )
        reply = "".join(
            getattr(b, "text", "") for b in response.content
            if getattr(b, "type", None) == "text"
        ).strip()
        if not reply:
            return jsonify({"briefing": None, "reason": "empty"})
        _cache.set(cache_key, reply, ttl=900)
        return jsonify({"briefing": reply, "cached": False})
    except anthropic.RateLimitError:
        return jsonify({"error": "Ski is at capacity — hourly AI quota reached."}), 429
    except anthropic.APIStatusError as exc:
        if exc.status_code == 529:
            return jsonify({"error": "Anthropic API is overloaded right now."}), 503
        return jsonify({"error": f"AI service error ({exc.status_code})"}), 502
    except Exception as exc:
        return jsonify({"error": f"AI service error: {exc}"}), 502


# ─────────────────────────────────────────────────────────────
# WebSocket Broadcasts
# ─────────────────────────────────────────────────────────────

def broadcast_price(
    symbol: str,
    price: float,
    change_pct: Optional[float] = None,
) -> None:
    """Broadcast price update."""
    socketio.emit(
        "price_update",
        {
            "symbol": symbol,
            "price": price,
            "change_pct": change_pct,
            "timestamp": time.time(),
        },
    )


def broadcast_alert(symbol: str, message: str) -> None:
    """Broadcast alert trigger."""
    socketio.emit(
        "alert_triggered",
        {
            "symbol": symbol,
            "message": message,
            "timestamp": time.time(),
        },
    )


# ─────────────────────────────────────────────────────────────
# Background price tracker
# ─────────────────────────────────────────────────────────────

def _background_tracker() -> None:
    """Fetch prices on an interval and push WebSocket updates."""
    raw_symbols = os.environ.get("STOCK_SYMBOLS", "AAPL,MSFT,TSLA,NVDA,AMZN")
    symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
    interval = int(os.environ.get("CHECK_INTERVAL", "60"))

    while True:
        for sym in symbols:
            try:
                price = get_stock_price(sym)
                if price is not None:
                    database.insert_price(sym, price)
                    socketio.emit(
                        "price_update",
                        {
                            "symbol": sym,
                            "price": price,
                            "timestamp": time.time(),
                        },
                        namespace="/stream",
                    )
            except Exception:
                pass
        time.sleep(interval)


# ─────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────

database.init_db()
socketio.start_background_task(_background_tracker)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
