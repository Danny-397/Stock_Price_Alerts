# Tradeski — DevPost Submission

**Live Demo:** https://tradeski.dev  
**GitHub:** https://github.com/Danny-397/Tradeski  
**Category:** Fintech / Real-Time Data / AI

---

## Inspiration

I've been fascinated by markets since I was a kid watching my dad check stock quotes. When I started learning to code, I wanted to build something I'd actually use — not another tutorial todo app. The problem I kept running into was that everything useful costs money. Bloomberg Terminal: $25,000/year. Real-time data APIs: hundreds of dollars a month. Even "free" tools like Robinhood or Yahoo Finance are fragmented — you get prices here, news there, macro data somewhere else entirely. Retail investors end up making decisions on incomplete, scattered information.

The idea behind Tradeski was simple: build the dashboard I wish existed. Not a toy, not a demo — a real, production-deployed financial intelligence platform with live prices, institutional-grade indicators, Federal Reserve macro data, and an AI assistant that actually understands what you're looking at. And build it in a way that forces genuine understanding of the underlying mathematics, not just API calls.

---

## What It Does

Tradeski is a full-stack real-time financial analytics platform deployed at [tradeski.dev](https://tradeski.dev). It integrates five distinct data pipelines into one coherent interface:

**Live Charts**  
Candlestick and line charts across six timeframes (1D through 1Y) with real yfinance data. Click any candle to see the exact OHLC, change %, and candle range in the data strip below. Overlay Bollinger Bands, SMA 20/50, EMA 20 — or add RSI and MACD as separate subpanels. Hit COMPARE to normalize up to five symbols to a common baseline and see relative performance at a glance.

**FRED Macro Ribbon**  
A live ribbon beneath the header shows CPI YoY%, Fed Funds Rate, GDP, Unemployment, 10-Year Treasury Yield, the Yield Curve spread, and High-Yield Credit Spreads — pulled hourly from the St. Louis Federal Reserve's FRED API.

**News Sentiment Feed**  
Recent headlines for any stock, scored with VADER sentiment analysis augmented by a custom financial lexicon. The generic VADER model is trained on social media — words like "beats" and "surges" are systematically under-scored for financial text. I added ~30 domain terms to correct this.

**Portfolio Tracker + Risk Metrics**  
Add your holdings by ticker, share count, and average cost. See unrealized P&L in real time. If you have positions, the dashboard automatically computes your portfolio's Sharpe ratio, beta vs. the S&P 500, and annualized volatility from one year of daily returns.

**Correlation Heatmap**  
A 90-day pairwise return correlation matrix across 11 major symbols, visualized as a Plotly heatmap with a diverging red-to-green colorscale. Useful for spotting diversification gaps and sector clustering at a glance.

**Ski — AI Financial Assistant**  
An AI chatbot powered by Anthropic Claude. What makes Ski different from a generic chatbot is context injection: every request automatically includes the current FRED macro snapshot, the user's portfolio (with live P&L), and recent news sentiment for the viewed symbol. Ski answers questions with awareness of what the user is actually looking at.

---

## How We Built It

**Backend:** Python + Flask + Flask-SocketIO running on Render with a gevent WebSocket worker. Real-time prices stream via WebSocket to connected clients. All API keys are environment variables — never committed.

**Data Layer:**
- **yfinance** for OHLC history and fundamental screener data
- **FRED API** for macroeconomic series (7 indicators, cached 1 hour)
- **NewsAPI** + **VADER** for news headlines and sentiment
- **SQLite** for portfolio and alert persistence

**Quantitative Indicators:** All ten indicators (RSI, MACD, Bollinger Bands, SMA, EMA, Z-Score, Volatility, ATR, Stochastic, Linear Regression) are written by hand on NumPy arrays in `tracker/analyzer.py` — no TA-Lib, no pandas (NumPy supplies only primitives like mean, std, and least-squares). This was a deliberate choice. Implementing Wilder's smoothing method by hand requires understanding *why* it diverges from a simple moving average. Writing the VADER financial lexicon requires reading enough headlines to know what the model gets wrong.

**Portfolio Risk:** Sharpe ratio, beta, and annualized volatility are computed by fetching one year of daily returns for each holding plus SPY, aligning arrays to the shortest series, weighting returns by current market value, and running the arithmetic directly on NumPy arrays.

**Correlation Matrix:** `numpy.corrcoef` over 90 days of aligned daily returns for 11 symbols. Cached server-side for one hour.

**Frontend:** Vanilla JavaScript (no framework), Plotly.js for charts, Socket.IO for WebSockets. The design is a custom terminal-style dark theme using CSS custom properties — Space Grotesk and JetBrains Mono for fonts.

**AI (Ski):** Anthropic Claude Haiku 4.5 via the `anthropic` SDK. The system prompt is a 700-word financial knowledge document. On every request, the backend injects the FRED snapshot, portfolio state, and news sentiment as additional context blocks. Flask-Limiter enforces 20 messages/hour per IP.

**Deployment:** Backend on Render (Python runtime, gevent WebSocket worker). Frontend on Vercel (static site). Custom domain `tradeski.dev` pointing to Vercel.

---

## Challenges We Ran Into

**WebSocket + Gunicorn:** Flask-SocketIO requires a gevent worker — not a standard gunicorn worker. Getting the `wsgi.py` to monkey-patch gevent *before* any other imports, and configuring the exact worker class (`geventwebsocket.gunicorn.workers.GeventWebSocketWorker`), took real debugging. If the patch order is wrong, the WebSocket silently falls back to long-polling with no error.

**CPI YoY% vs. raw index:** The FRED CPIAUCSL series returns the raw index value (~332), not a percentage. I initially displayed this as "332%" in the macro ribbon. The fix was adding the `units=pc1` (percent change from year ago) transform parameter to the FRED API request — but that required reading through the FRED API documentation to find it.

**Toolbar width at 100% zoom:** The original single-row toolbar ran out of horizontal space at 100% browser zoom, causing the LINE/CANDLE/COMPARE buttons to stack vertically. The fix was restructuring into two rows — chart type and timeframes on row 1, overlays and subpanels on row 2 — with CSS flex containers.

**Ski going off-screen on small viewports:** The Ski panel had a fixed `height: 520px`. On laptop screens with a status bar, this pushed the input box below the visible area. Fixed by replacing the fixed height with `max-height: calc(100vh - var(--status-h) - 90px)`.

**VADER financial domain coverage:** Standard VADER is trained on social media text and consistently under-scores financial language. "Beats earnings" scores near neutral. "Bankruptcy" scores barely negative. I built a custom financial lexicon patch (~30 terms) to correct the most systematically wrong valuations.

**Plotly multi-panel layouts:** Adding RSI and MACD as separate subpanels requires dynamic domain recalculation on every render. With zero, one, or two subpanels active, the main chart domain, subpanel domains, and shared x-axis anchors all change. Getting the layout math right so that switching panels doesn't leave gaps or collapse panels took careful work.

---

## Accomplishments We're Proud Of

**Zero external indicator libraries.** Every quantitative signal — RSI, MACD, Bollinger Bands, Z-Score, Volatility, ATR, Stochastic, Linear Regression — is written by hand on NumPy (no TA-Lib, no pandas). Not because it's faster, but because it required genuine understanding of the mathematics.

**AI grounded in live data.** Ski isn't just a chatbot with a financial system prompt. It has access to the actual macro environment, the user's portfolio with live P&L, and current news sentiment. Most "AI financial assistants" answer in a vacuum. Ski doesn't.

**Full production deployment.** This is a live, publicly accessible platform at tradeski.dev — not a localhost demo or a screenshot in a slide deck. Real traffic, real data, real WebSocket connections.

**Five data pipelines, one interface.** Prices, macro data, news sentiment, portfolio analytics, and AI all talking to each other through a single coherent interface. Each pipeline alone would be a reasonable project. The integration is where the real engineering happened.

**70 passing tests.** Every indicator has correctness tests — including regression tests for the all-gains RSI boundary and the high/low-based Stochastic. The FRED client has mocked HTTP tests. The database has CRUD round-trip tests. Auth has password-hash, token, and per-user isolation tests. The sentiment pipeline has financial lexicon augmentation tests. CI runs all 70 on every push.

---

## What We Learned

I learned how financial indicators actually work — not at the "RSI measures momentum" level, but at the "Wilder's smoothing diverges from SMA because it weights recent periods exponentially and that matters at the edges of a time series" level. The same pattern repeated across every indicator: implementing from scratch surfaces the assumptions that library documentation glosses over.

I learned how to think about context injection for LLMs. The hard part isn't getting an AI to answer financial questions — it's getting it to answer questions *about your situation*, not generic situations. That requires grounding the model in structured live data, which requires a thoughtful context pipeline.

I learned how much a product is shaped by deployment constraints. Render's free tier spins down after inactivity. That one fact drove design decisions: the `/health` endpoint, the caching layer, the "backend is spinning up" error message in the Ski panel. Real constraints produce better engineering than imaginary ones.

---

## What's Next

**Accounts & saved data** — Shipped: optional email/password accounts (PBKDF2-hashed) now persist each user's portfolio, alerts, and watchlist server-side via signed bearer tokens, with every record scoped by `user_id`. Next: surface the persistent watchlist in the dashboard UI and add password reset.

**Options chain data** — Integrate IV, Greeks, and open interest from a free options API alongside the underlying price chart.

**Alert notifications** — Pushover integration is already built in (`notifier.py`); the production deployment just needs the credentials wired up.

**More tickers in the correlation matrix** — The heatmap is currently limited to 11 symbols. Expanding to user-defined watchlists would make it more personalized.

**Backtesting** — The indicator infrastructure is already in place; the next step is adding a simple backtesting engine that tests strategies like "buy when RSI < 30, sell when RSI > 70" against historical data.
