from tracker.analyzer import (
    sma,
    ema,
    rsi,
    bollinger_bands,
    macd,
    zscore,
    volatility,
    linear_regression_prediction,
    analyze_series,
)


def test_sma_basic():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    result = sma(values, 3)
    # First two are None, then 2, 3, 4
    assert result[:2] == [None, None]
    assert result[2:] == [2.0, 3.0, 4.0]


def test_ema_basic():
    values = [1.0, 2.0, 3.0, 4.0]
    result = ema(values, 2)
    assert len(result) == len(values)
    # EMA should be strictly increasing for strictly increasing input
    assert all(result[i] < result[i + 1] for i in range(len(result) - 1))


def test_rsi_length_and_range():
    # Mixed up/down moves so RSI lands strictly inside (0, 100).
    values = [1.0, 2.0, 1.5, 3.0, 2.5, 4.0, 3.5, 5.0, 4.0, 6.0,
              5.5, 7.0, 6.0, 8.0, 7.0, 9.0, 8.0, 10.0, 9.0, 11.0]
    result = rsi(values, 14)
    assert len(result) == len(values)
    numeric = [v for v in result if v is not None]
    assert numeric
    assert all(0 <= v <= 100 for v in numeric)
    # A series with both gains and losses must not pin to an extreme.
    assert all(0 < v < 100 for v in numeric)


def test_bollinger_bands_shapes():
    values = list(float(i) for i in range(1, 60))
    upper, lower = bollinger_bands(values, period=20)
    assert len(upper) == len(values)
    assert len(lower) == len(values)
    # After warmup, upper should be >= lower
    for u, l in zip(upper[25:], lower[25:]):
        assert u is None or l is None or u >= l


def test_macd_shapes():
    values = list(float(i) for i in range(1, 80))
    macd_line, signal_line, hist = macd(values)
    assert len(macd_line) == len(values)
    assert len(signal_line) == len(values)
    assert len(hist) == len(values)


def test_zscore_basic():
    values = list(float(i) for i in range(1, 60))
    result = zscore(values, period=20)
    assert len(result) == len(values)
    # First 20 should be None
    assert all(v is None for v in result[:20])
    # Later values should be numeric
    numeric = [v for v in result[25:] if v is not None]
    assert numeric
    assert all(isinstance(v, float) for v in numeric)


def test_volatility_basic():
    values = list(float(i) for i in range(1, 60))
    result = volatility(values, period=20)
    assert len(result) == len(values)
    assert all(v is None for v in result[:20])
    numeric = [v for v in result[25:] if v is not None]
    assert numeric
    assert all(v >= 0 for v in numeric)


def test_linear_regression_prediction_basic():
    values = list(float(i) for i in range(1, 50))
    pred = linear_regression_prediction(values)
    assert pred is not None
    # For a perfect line y = x, next prediction should be close to 50
    assert 49.0 <= pred <= 51.0


def test_analyze_series_empty():
    result = analyze_series([])
    assert result == {}
