"""Edge-case coverage for the indicator library: empty input, flat series,
single-element arrays, and the all-gains / all-losses RSI boundaries."""

from tracker.analyzer import (
    sma,
    ema,
    rsi,
    bollinger_bands,
    zscore,
    volatility,
    stochastic,
    linear_regression_prediction,
    analyze_series,
)


# ── Empty / degenerate input ─────────────────────────────────────────────

def test_sma_shorter_than_period_is_all_none():
    assert sma([1.0, 2.0], 5) == [None, None]


def test_ema_empty():
    assert ema([], 10) == []


def test_bollinger_shorter_than_period():
    upper, lower = bollinger_bands([1.0, 2.0], period=20)
    assert upper == [None, None]
    assert lower == [None, None]


def test_linear_regression_too_few_points():
    assert linear_regression_prediction([1.0, 2.0, 3.0]) is None


# ── Flat series ──────────────────────────────────────────────────────────

def test_volatility_flat_series_is_zero():
    result = volatility([5.0] * 30, period=20)
    numeric = [v for v in result if v is not None]
    assert numeric
    assert all(v == 0 for v in numeric)


def test_zscore_flat_series_is_zero():
    result = zscore([5.0] * 30, period=20)
    numeric = [v for v in result if v is not None]
    assert numeric
    assert all(v == 0 for v in numeric)


def test_rsi_flat_series_is_neutral():
    # No up or down moves -> RSI is neutral (50), not overbought/oversold.
    result = rsi([5.0] * 30, period=14)
    numeric = [v for v in result if v is not None]
    assert numeric
    assert all(v == 50.0 for v in numeric)


# ── RSI directional boundaries (the inverted-RSI regression) ─────────────

def test_rsi_pure_uptrend_is_max():
    # Strictly increasing series has zero losses -> RSI must pin to 100,
    # not collapse to 0. Guards against the rs = 0 shortcut on down == 0.
    result = rsi([float(i) for i in range(1, 40)], period=14)
    numeric = [v for v in result if v is not None]
    assert numeric
    assert all(v == 100.0 for v in numeric)


def test_rsi_pure_downtrend_is_min():
    result = rsi([float(i) for i in range(40, 1, -1)], period=14)
    numeric = [v for v in result if v is not None]
    assert numeric
    assert all(v == 0.0 for v in numeric)


# ── Stochastic uses real high/low, and analyze_series stays honest ───────

def test_stochastic_uses_high_low_range():
    highs = [float(i) + 1 for i in range(20)]
    lows = [float(i) - 1 for i in range(20)]
    closes = [float(i) for i in range(20)]
    k_vals, d_vals = stochastic(highs, lows, closes)
    assert len(k_vals) == len(closes)
    numeric = [v for v in k_vals if v is not None]
    assert all(0 <= v <= 100 for v in numeric)


def test_analyze_series_omits_stochastic_without_ohlc():
    # Close-only series: stochastic can't be computed, so it must be None
    # rather than fabricated from close-as-high/low.
    data = [("t", float(i)) for i in range(60)]
    result = analyze_series(data)
    assert result["stoch_k"] is None
    assert result["stoch_d"] is None
    assert result["rsi14"] is not None


def test_analyze_series_computes_stochastic_with_ohlc():
    data = [("t", float(i)) for i in range(60)]
    highs = [float(i) + 1 for i in range(60)]
    lows = [float(i) - 1 for i in range(60)]
    result = analyze_series(data, highs=highs, lows=lows)
    assert result["stoch_k"] is not None


def test_analyze_series_empty():
    assert analyze_series([]) == {}
