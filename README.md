<div align="center">

# Tradeski

**A real-time financial analytics platform for retail investors.**

Live charts · Federal Reserve macro data · AI-powered assistant · Portfolio risk analytics · News sentiment

[![CI](https://github.com/Danny-397/Tradeski/actions/workflows/ci.yml/badge.svg)](https://github.com/Danny-397/Tradeski/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-70%20passing-22c55e.svg)](#running-tests)
[![Deploy: Render](https://img.shields.io/badge/backend-Render-46E3B7?logo=render&logoColor=white)](https://render.com)
[![Deploy: Vercel](https://img.shields.io/badge/frontend-Vercel-000000?logo=vercel&logoColor=white)](https://vercel.com)

### [→ tradeski.dev](https://tradeski.dev)

</div>

---

Tradeski integrates five distinct data pipelines — live equity prices, Federal Reserve macroeconomic data, financial news with sentiment scoring, a fundamental stock screener, and an AI assistant grounded in real-time context — into a single coherent interface, with quantitative portfolio analytics and a 90-day correlation heatmap layered on top.

Every quantitative indicator is hand-written directly on NumPy arrays — no TA-Lib, no pandas — so the indicator logic itself (Wilder smoothing, MACD divergence, band math) is the project's own code, with NumPy supplying only primitive operations like mean, standard deviation, and least-squares. The AI assistant (Ski) receives live macro, portfolio, and news data on every request — not generic training knowledge. The backend is a persistent WebSocket server deployed on Render; the frontend is a static site on Vercel.

| | |
|---|---|
| **Backend** | Python 3.12 · Flask · Flask-SocketIO · gevent |
| **Frontend** | Vanilla JS · Plotly.js · Socket.IO |
| **Data** | yfinance · FRED API · NewsAPI · Anthropic Claude |
| **Tests** | 70 tests across 10 files — CI on every push |
| **Deployment** | Render (backend) · Vercel (frontend) · tradeski.dev |

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Quantitative Indicators](#quantitative-indicators)
- [Ski — AI Financial Assistant](#ski--ai-financial-assistant)
- [Production Hardening](#production-hardening)
- [API Reference](#api-reference)
- [Getting Started](#getting-started)
- [Running Tests](#running-tests)
- [Deployment](#deployment)
- [Background](#background)

---

## Features

### Real-Time Price Streaming

Live equity prices broadcast over WebSocket (Socket.IO / gevent) with automatic chart extension, a scrolling ticker tape across 11 symbols, and color-coded flash animations on every price update.

### Interactive Charts

| Capability | Detail |
|:---|:---|
| Chart types | Candlestick (OHLC) and line chart |
| Timeframes | 1D (5-min bars) · 5D (15-min) · 1M · 3M · 6M · 1Y (daily) |
| Overlays | Bollinger Bands · SMA 20/50 · EMA 20 — each independently toggleable |
| Subpanels | RSI and MACD as separate panels below the main chart |
| Click-to-inspect | Click any candle to populate the data strip with exact OHLC, change %, and range |
| Compare mode | Normalize up to 5 symbols to a common 0% baseline for relative performance |

### Macroeconomic Ribbon

A live ribbon beneath the header pulls seven Federal Reserve series from the FRED API, refreshed hourly. Each indicator displays its current value, trend direction (↑ / ↓), and date of last observation.

| FRED Series | Metric |
|:---|:---|
| CPIAUCSL | Consumer Price Index (YoY %) |
| FEDFUNDS | Effective Federal Funds Rate |
| GDP | Real GDP (Chained 2017 $B) |
| UNRATE | Unemployment Rate |
| DGS10 | 10-Year Treasury Yield |
| T10Y2Y | Yield Curve Spread (10Y − 2Y) |
| BAMLH0A0HYM2 | High-Yield Credit Spread (OAS) |

### News Feed & Sentiment Analysis

Recent headlines per symbol are fetched from NewsAPI and scored with VADER sentiment analysis. The standard VADER lexicon is augmented with ~30 financial-domain terms (`"beats" → +2.0`, `"bankruptcy" → −3.0`, `"downgraded" → −1.8`) to correct systematic under-scoring of financial jargon. Each article shows a BULLISH / BEARISH / NEUTRAL chip with its numeric score, and an aggregate sentiment badge summarizes the overall tone.

### Portfolio Tracker & Risk Analytics

Add holdings by symbol, share count, and average cost basis. See unrealized P&L in real time, persisted in SQLite via UPSERT. The risk metrics panel computes three statistics from one year of daily returns weighted by current market value:

| Metric | Method |
|:---|:---|
| Sharpe Ratio | Annualized excess return / annualized volatility · risk-free rate: 4.5% |
| Portfolio Beta | Covariance with SPY / variance of SPY over 252 trading days |
| Annualized Volatility | Rolling standard deviation of weighted daily returns × √252 |

### Correlation Heatmap

A full-screen modal shows a pairwise 90-day return correlation matrix for all 11 tracked symbols, rendered as a Plotly heatmap with a diverging red-to-green colorscale. Cached server-side for one hour.

### Alert Engine

Rule-based alerts — price above/below threshold, RSI overbought (>70) / oversold (<30), volume spike, volatility spike — with cooldown tracking, optional Pushover push notifications, and SQLite persistence across restarts.

### Stock Screener

Fundamental data for 28 curated stocks across five sectors, fetched in parallel via an 8-worker thread pool and cached per symbol. Filterable by P/E ratio, market cap tier, sector, and 52-week performance. Clicking a result loads that symbol on the chart.

### Accounts & Saved Data

Browsing, charting, the screener, and Ski work without an account. Creating a free account (email + password) persists your **portfolio, alerts, and watchlist** server-side so they survive across sessions and devices. Passwords are stored only as salted PBKDF2 hashes; sessions use signed, expiring bearer tokens (no server-side session table). Every personal record is scoped to its owner — the portfolio and alert tables are keyed by `user_id`, so one account can never read or mutate another's data. A published [Privacy Policy](frontend/privacy.html) documents exactly what is collected and why.

---

## Architecture

```
Tradeski/
│
├── tracker/                   # Core Python backend module
│   ├── analyzer.py            # 10 quantitative indicators, written by hand on NumPy
│   ├── auth.py                # Password hashing (PBKDF2) + signed bearer tokens
│   ├── database.py            # SQLite persistence — prices, alerts, portfolio, users, watchlist
│   ├── price_fetcher.py       # yfinance: live prices, OHLC history, screener data
│   ├── fred.py                # FRED API client — 7 macro series with trend detection
│   ├── news.py                # NewsAPI + VADER sentiment with financial lexicon
│   ├── alerts.py              # Rule-based alert evaluation engine
│   ├── notifier.py            # Pushover push notification handler
│   ├── scheduler.py           # APScheduler background job manager
│   ├── daily_summary.py       # Daily price/volume summary generator
│   ├── pruning.py             # Automatic 30-day data retention cleanup
│   ├── config.py              # Environment variable configuration
│   ├── logger.py              # Rotating file + console logger
│   └── main.py                # Real-time tracker entry point
│
├── Plotly dashboard/          # Flask + Socket.IO application server
│   ├── app.py                 # REST API, WebSocket emitters, Ski /chat endpoint
│   └── cache.py               # TTL in-memory cache (SimpleCache)
│
├── frontend/                  # Static UI — served via Vercel
│   ├── landing.html           # Landing page at tradeski.dev/
│   ├── index.html             # Dashboard at tradeski.dev/app
│   ├── privacy.html           # Privacy Policy at tradeski.dev/privacy.html
│   ├── 404.html               # Custom terminal-style 404 page
│   ├── favicon.svg            # SVG favicon
│   ├── styles.css             # Terminal dark theme — CSS custom properties, mobile-responsive
│   └── dashboard.js           # WebSocket, Plotly, Ski, screener, health indicator
│
├── tests/                     # pytest suite — 70 tests across 10 files
│   ├── test_analyzer.py       # Indicator shape, value range, arithmetic correctness
│   ├── test_analyzer_basic.py # Edge cases: empty input, flat series, single-element
│   ├── test_database.py       # SQLite round-trips for prices, alerts, portfolio
│   ├── test_fred.py           # FRED client with mocked HTTP — trend detection
│   ├── test_news.py           # VADER: financial lexicon augmentation, aggregation
│   ├── test_portfolio.py      # CRUD operations, UPSERT behavior
│   ├── test_screener.py       # Data normalization, ETF fallback, edge cases
│   ├── test_notifier.py       # Pushover mock: correct POST structure
│   └── test_price_fetcher.py  # Live yfinance smoke test
│
├── .github/workflows/ci.yml   # GitHub Actions: lint → test on every push
├── render.yaml                # Render deployment config (gunicorn + gevent worker)
├── vercel.json                # Vercel routing config
├── wsgi.py                    # WSGI entry point with gevent monkey-patching
├── requirements.txt           # Python dependencies
├── setup.cfg                  # Flake8 configuration
└── .env.example               # All required environment variables documented
```

### Data Flow

```
┌──────────────────────────────────────────────────────────────────┐
│  External Data Sources                                           │
│  yfinance  ·  FRED API  ·  NewsAPI  ·  Anthropic Claude         │
└────────┬──────────┬────────────┬──────────────────┬─────────────┘
         │          │            │                  │
         ▼          ▼            ▼                  ▼
   price_fetcher  fred.py     news.py          /chat endpoint
         │          │            │             (Haiku 4.5)
         ▼          │            │                  │
   database.py      │            │                  │
   (SQLite)         │            │                  │
         │          │            │                  │
         ▼          ▼            ▼                  │
     analyzer.py  macro       VADER             Ski panel
     (indicators) snapshot    scores            (frontend)
         │          │            │
         └──────────┴────────────┘
                    │
                    ▼
             app.py (Flask)
           ┌──────────────────┐
           │  REST Endpoints  │   WebSocket
           │  /health         │──────────────► price_update
           │  /stats          │──────────────► alert_triggered
           │  /price_history  │
           │  /macro          │
           │  /news           │
           │  /portfolio      │
           │  /screener       │
           │  /chat           │
           └────────┬─────────┘
                    │ JSON / WS
                    ▼
             dashboard.js
          (Plotly · Socket.IO)
```

---

## Tech Stack

| Layer | Technology |
|:---|:---|
| Language | Python 3.12 |
| Web Framework | Flask + Flask-SocketIO |
| Real-Time Transport | WebSocket (Socket.IO / gevent) |
| Database | SQLite (`sqlite3` stdlib) |
| Auth | PBKDF2 password hashing (werkzeug) · signed bearer tokens (itsdangerous) |
| Market Data | yfinance (Yahoo Finance) |
| Macro Data | FRED API (St. Louis Federal Reserve) |
| News & Sentiment | NewsAPI + VADER with financial lexicon |
| AI Assistant | Anthropic Claude Haiku 4.5 |
| Parallel Fetching | `concurrent.futures.ThreadPoolExecutor` |
| Scheduler | APScheduler |
| Notifications | Pushover API (optional) |
| Frontend Charting | Plotly.js |
| Frontend Runtime | Vanilla JavaScript (ES2020) |
| Fonts | Space Grotesk · JetBrains Mono |
| CI/CD | GitHub Actions |
| Linting | Flake8 |
| Testing | pytest (70 tests) |
| Backend Hosting | Render |
| Frontend Hosting | Vercel |

---

## Quantitative Indicators

All ten indicators live in [`tracker/analyzer.py`](tracker/analyzer.py) and are written by hand on NumPy arrays — no TA-Lib, no pandas. NumPy provides only the primitive numerics (mean, standard deviation, least-squares); the indicator logic itself is implemented directly, which requires genuine engagement with the underlying mathematics.

| Indicator | Implementation detail |
|:---|:---|
| SMA (20, 50) | Each window computed independently to avoid floating-point drift |
| EMA (20) | Standard `k = 2/(n+1)` multiplier |
| RSI (14) | Wilder smoothing — industry-standard formulation, less volatile than SMA-based RSI |
| MACD | 12/26 EMA divergence with 9-period signal line and histogram |
| Bollinger Bands | ±2σ bands around SMA-20 |
| Z-Score | Rolling 20-period deviation from mean |
| Volatility | Rolling standard deviation proxy for realized vol |
| ATR (14) | True range smoothed via Wilder's method — matches Wilder (1978) spec |
| Stochastic | %K/%D oscillator with configurable lookback |
| Linear Regression | Least-squares next-value prediction via `numpy.linalg.lstsq` |

**RSI — Wilder Smoothing:** Initial up/down averages use an arithmetic mean over the seed period. Subsequent values: `avg = (prev × (period − 1) + current) / period`. Output range: [0, 100].

**ATR — True Range:** `max(High − Low, |High − PrevClose|, |Low − PrevClose|)`, then Wilder-smoothed to match the original specification.

---

## Ski — AI Financial Assistant

Ski is powered by **Anthropic Claude Haiku 4.5** and grounded in three live data sources injected on every request. Most AI financial tools answer questions in a vacuum — Ski answers questions about your situation.

| Context block | Source | Content |
|:---|:---|:---|
| System prompt | Static | Financial knowledge: equities, macro, sector rotation, indicator signals |
| Macro context | FRED API (cached 1h) | CPI, Fed Funds Rate, GDP, unemployment, yield curve, credit spreads |
| Portfolio context | SQLite (live) | Per-holding weight, live P&L, portfolio risk (Sharpe/beta/vol), concentration flag, watchlist |
| News context | NewsAPI + VADER (cached 30m) | Recent headlines for the viewed symbol with sentiment scores |

Ski can answer "Is now a good time to add to my NVDA position?" with awareness of the user's actual cost basis, the current macro environment, and recent news sentiment — not boilerplate.

**Rate limiting:** 20 messages/hour · 50 messages/day per IP via Flask-Limiter.

---

## Production Hardening

### Security

| Measure | Implementation |
|:---|:---|
| Authentication | Salted PBKDF2 password hashing (werkzeug) · signed, expiring bearer tokens (itsdangerous) · per-`user_id` row scoping so accounts are isolated |
| Rate limiting | Flask-Limiter: **10 req/min** per IP globally · **20/hr + 50/day** on `/chat` · **10/min** on `/auth/login` · **10/hr** on `/auth/register` |
| Input sanitization | Strips HTML tags via regex · 500-character hard limit on chat messages · email/password validation on signup |
| Symbol sanitization | Ticker restricted to `[A-Z0-9.]` — no injection vectors |
| CORS | Restricted to `tradeski.dev` and `www.tradeski.dev` via `ALLOWED_ORIGINS` env var |
| Secrets | All API keys read from environment variables exclusively — never committed |

### Reliability

| Measure | Implementation |
|:---|:---|
| TTL caching | FRED 1hr · prices 5min · news 30min · screener 10min · correlation 1hr |
| Health endpoint | `GET /health` returns live API service status — `configured` vs `missing` |
| Graceful AI errors | `RateLimitError` → 429 · `529 overloaded` → 503 with retry guidance |
| Frontend error UX | Distinct messages for 429, 503, and network failures |

### Frontend

| Measure | Implementation |
|:---|:---|
| Landing page | `/` serves a dark marketing page · dashboard lives at `/app` |
| Custom 404 | Terminal-style 404 page with path display and navigation |
| Mobile responsive | Portrait: slide-in drawer · Landscape: full desktop layout |
| API health indicator | Green / yellow / red dot in the header reflects live `/health` status |
| Analytics | Vercel Analytics via `/_vercel/insights/script.js` |

---

## API Reference

### `GET /health`
Returns live status for all three external API integrations.

```json
{
  "status": "ok",
  "services": {
    "fred":      "configured",
    "news":      "configured",
    "anthropic": "configured"
  }
}
```

### `GET /stats?symbol=AAPL`
OHLC snapshot + real 52-week range from yfinance. Cached 5 minutes.

```json
{
  "symbol": "AAPL",
  "open": 189.42, "high": 191.05, "low": 188.30, "close": 190.67,
  "high_52w": 199.62, "low_52w": 143.90, "change_pct": 0.66
}
```

### `GET /price_history?symbol=AAPL&tf=1M`
Full OHLC + indicator payload. All arrays are index-aligned to `timestamps`. Valid `tf`: `1D` `5D` `1M` `3M` `6M` `1Y`.

```json
{
  "timestamps": ["2024-01-02T00:00:00", "..."],
  "open": [], "high": [], "low": [], "close": [],
  "sma20": [], "sma50": [], "ema20": [],
  "rsi": [], "macd": [], "signal": [], "histogram": [],
  "upper_band": [], "lower_band": [],
  "zscore": [], "volatility": []
}
```

### `GET /macro`
Current FRED macro snapshot. Cached 1 hour. Requires `FRED_API_KEY`.

```json
{
  "CPIAUCSL": { "label": "CPI", "value": 3.2, "unit": "%", "trend": "down", "date": "2024-10-01" },
  "FEDFUNDS": { "label": "Fed Funds Rate", "value": 5.33, "unit": "%", "trend": "neutral", "date": "..." }
}
```

### `GET /news?symbol=AAPL`
VADER-scored headlines + aggregate sentiment. Cached 30 minutes. Requires `NEWS_API_KEY`.

```json
{
  "articles": [{
    "title": "Apple beats Q4 earnings expectations",
    "source": "Reuters",
    "published_at": "2024-11-01T12:00:00Z",
    "sentiment": 0.612,
    "sentiment_label": "bullish"
  }],
  "aggregate": { "score": 0.341, "label": "bullish", "count": 10 }
}
```

### `POST /auth/register` · `POST /auth/login`
Create an account or sign in. Body: `{ "email": "you@example.com", "password": "…" }`. Returns a bearer token and the user record. Passwords are hashed with PBKDF2; the token is a signed, 30-day itsdangerous token.

```json
{ "token": "…", "user": { "id": 1, "email": "you@example.com" } }
```

### `GET /auth/me`
Returns the signed-in user (requires `Authorization: Bearer <token>`).

### `GET /portfolio` · `POST /portfolio` · `DELETE /portfolio/<id>`
List holdings with live P&L, add/update a position (UPSERT on symbol), or remove one. **Requires authentication**; all holdings are scoped to the signed-in user.

`POST` body: `{ "symbol": "AAPL", "shares": 10, "avg_cost": 175.00 }`

### `GET /portfolio/risk`
Sharpe ratio, beta vs. S&P 500, and annualized volatility from 1-year daily returns.

```json
{ "sharpe": 1.24, "beta": 0.91, "volatility": 18.4 }
```

### `GET /correlation`
Pairwise 90-day return correlation matrix for all tracked symbols. Cached 1 hour.

```json
{
  "symbols": ["AAPL", "MSFT", "NVDA", "..."],
  "matrix": [[1.0, 0.874, 0.761], ["..."]]
}
```

### `GET /screener`
Fundamental data for the full 28-stock universe. Parallel fetch, cached 10 min per symbol.

### `GET /watchlist/saved` · `POST /watchlist/saved` · `DELETE /watchlist/saved/<symbol>`
Read, add to, or remove from the signed-in user's persistent watchlist. **Requires authentication.**

### `GET /alerts` · `POST /alerts` · `DELETE /alerts/<id>`
List, create, and delete rule-based price and indicator alerts. **Requires authentication**; alerts are scoped to the signed-in user.

### `POST /chat`
Ski chatbot. Body: `{ "message": "...", "history": [...], "symbol": "AAPL" }`.
Injects live macro, portfolio, and news context. Requires `ANTHROPIC_API_KEY`.

### WebSocket Events

| Event | Direction | Payload |
|:---|:---|:---|
| `price_update` | Server → Client | `{ symbol, price, change_pct, timestamp }` |
| `alert_triggered` | Server → Client | `{ symbol, message, timestamp }` |

---

## Getting Started

### Prerequisites

- Python 3.12+
- pip

### Install

```bash
git clone https://github.com/Danny-397/Tradeski.git
cd Tradeski
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Open `.env` and fill in the three required keys:

| Variable | Where to get it | Required? |
|:---|:---|:---|
| `SECRET_KEY` | Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` | Yes in production — signs auth tokens; unset = sessions reset on restart |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | Yes — powers Ski |
| `FRED_API_KEY` | [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html) | No — macro ribbon disabled without it |
| `NEWS_API_KEY` | [newsapi.org/register](https://newsapi.org/register) | No — news panel disabled without it |

### Run

```bash
# Terminal 1 — backend
cd "Plotly dashboard"
python app.py
# → http://localhost:5000

# Terminal 2 — frontend
python -m http.server 8080 --directory frontend
# → http://localhost:8080
```

---

## Running Tests

```bash
python -m pytest -v
```

70 tests across 10 files:

| File | What it covers |
|:---|:---|
| `test_analyzer.py` | Indicator shape, value range, arithmetic correctness |
| `test_analyzer_basic.py` | Edge cases: empty input, flat series, single-element arrays |
| `test_database.py` | SQLite round-trips for prices, alerts, portfolio |
| `test_fred.py` | FRED client with mocked HTTP — trend detection, missing series |
| `test_news.py` | VADER: financial lexicon augmentation, aggregation |
| `test_portfolio.py` | CRUD operations, UPSERT behavior, case-insensitive symbols |
| `test_screener.py` | Data normalization, ETF sector fallback, edge cases |
| `test_notifier.py` | Pushover mock: correct POST structure, skip without credentials |
| `test_price_fetcher.py` | Live yfinance smoke test |

```bash
flake8 .   # linting
```

GitHub Actions runs lint and the full test suite on every push and pull request to `main`.

---

## Deployment

### Backend — Render

1. Connect the repo to Render and select `render.yaml` for configuration.
2. Set all environment variables in the Render dashboard → **Environment** tab. At minimum set **`SECRET_KEY`** (signs auth tokens) and the API keys; set **`DATABASE_URL`** for persistence (see below).
3. `render.yaml` configures a gunicorn + gevent WebSocket worker — do not change the worker class.
4. Render provisions a public HTTPS URL with persistent WebSocket connections.

### Persistence — Postgres (recommended in production)

The app uses SQLite by default, which is perfect for local development. On Render's free tier the SQLite file lives on an **ephemeral disk that is wiped on every redeploy and sleep**, so user accounts and saved portfolios would not survive. To persist them:

1. Create a free Postgres database (e.g. [Neon](https://neon.tech)) and copy its connection string.
2. Set **`DATABASE_URL`** to that string in the Render dashboard.
3. Redeploy. `tracker/database.py` detects the `postgres://`/`postgresql://` URL and uses Postgres automatically (tables are created on boot); with no `DATABASE_URL` it transparently falls back to SQLite. `GET /health` reports the active backend as `"db": "postgres"` or `"sqlite"`.

### Frontend — Vercel

1. Connect the repo to Vercel. Vercel reads `vercel.json` automatically.
2. Update `CFG.API` and `CFG.WS` in [`frontend/dashboard.js`](frontend/dashboard.js) to your Render URL.
3. Push — Vercel deploys in under 30 seconds.

---

## Background

The tools professional traders use daily — live indicators, macro dashboards, portfolio analytics, AI-assisted research — are locked behind expensive terminals or scattered across a dozen websites. Bloomberg Terminal costs $25,000/year. Retail investors end up making decisions on stale, fragmented information.

The goal was to build a platform that integrates all of those data sources into a single coherent interface — and to build it properly. That meant implementing every quantitative indicator by hand rather than importing an indicator library. It meant wiring a real macroeconomic data pipeline directly from the Federal Reserve. It meant building an AI assistant that knows what the user is actually looking at, not one answering questions in a vacuum.

Writing Wilder's smoothing from scratch requires understanding why it diverges from a simple moving average and what that difference means at the edges of a time series. Writing the VADER financial lexicon requires reading enough headlines to know that "beats" and "surges" are systematically under-scored by a general-purpose sentiment model. These are not problems that get solved by importing a package.

Tradeski spans six engineering disciplines in one codebase:

- **Data engineering** — real-time polling, SQLite schema design, TTL caching, 30-day retention pruning
- **Backend architecture** — REST API design, WebSocket streaming, background scheduling, parallel HTTP fetching
- **Quantitative analysis** — ten financial indicators written by hand on NumPy; Sharpe ratio, beta, and volatility from raw daily return vectors
- **Statistical computing** — pairwise correlation matrix over 90-day return series; portfolio variance weighted by live market value
- **AI integration** — context injection pipeline, grounding an LLM in live structured data
- **Frontend engineering** — real-time state management, multi-library charting, normalized multi-stock overlays, terminal-grade UI

---

## License

MIT © 2026 Dan Lichtenberger. See [LICENSE](LICENSE).
