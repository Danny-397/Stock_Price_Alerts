"""Technical indicator calculations for price series."""

from typing import List, Tuple, Optional
import numpy as np


def sma(values: List[float], period: int) -> List[Optional[float]]:
    """Simple Moving Average."""
    if len(values) < period:
        return [None] * len(values)

    result: List[Optional[float]] = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        result.append(sum(window) / period)
    return result


def ema(values: List[float], period: int) -> List[float]:
    """Exponential Moving Average."""
    if not values:
        return []

    k = 2 / (period + 1)
    ema_val = values[0]
    result: List[float] = []

    for price in values:
        ema_val = price * k + ema_val * (1 - k)
        result.append(ema_val)

    return result


def rsi(values: List[float], period: int = 14) -> List[Optional[float]]:
    """Relative Strength Index using Wilder smoothing."""
    if len(values) < period + 1:
        return [None] * len(values)

    deltas = np.diff(values)
    seed = deltas[:period]

    up = seed[seed > 0].sum() / period
    down = -seed[seed < 0].sum() / period

    result: List[Optional[float]] = [None] * period
    result.append(_rsi_value(up, down))

    for delta in deltas[period:]:
        up_val = max(delta, 0)
        down_val = -min(delta, 0)

        up = (up * (period - 1) + up_val) / period
        down = (down * (period - 1) + down_val) / period

        result.append(_rsi_value(up, down))

    return result


def _rsi_value(up: float, down: float) -> float:
    """Convert average gain/loss into an RSI reading in [0, 100].

    When the average loss is zero the relative strength diverges to
    infinity, so RSI is 100 (max overbought) if there were any gains, or a
    neutral 50 for a perfectly flat window. The naive ``rs = 0`` shortcut
    inverts this and returns 0, so it is handled explicitly here.
    """
    if down == 0:
        return 100.0 if up > 0 else 50.0
    rs = up / down
    return 100 - (100 / (1 + rs))


def bollinger_bands(
    values: List[float],
    period: int = 20,
    std_factor: float = 2.0,
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Return upper and lower Bollinger Bands."""
    if len(values) < period:
        none_list = [None] * len(values)
        return none_list, none_list

    sma_vals = sma(values, period)
    upper: List[Optional[float]] = []
    lower: List[Optional[float]] = []

    for i in range(len(values)):
        if i < period - 1:
            upper.append(None)
            lower.append(None)
            continue

        window = values[i - period + 1:i + 1]
        std = float(np.std(window))

        upper.append(sma_vals[i] + std_factor * std)
        lower.append(sma_vals[i] - std_factor * std)

    return upper, lower


def macd(
    values: List[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Tuple[List[float], List[float], List[float]]:
    """MACD line, signal line, and histogram."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)

    macd_line = [a - b for a, b in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, signal_period)
    histogram = [m - s for m, s in zip(macd_line, signal_line)]

    return macd_line, signal_line, histogram


def zscore(values: List[float], period: int = 20) -> List[Optional[float]]:
    """Rolling Z-score."""
    result: List[Optional[float]] = []

    for i in range(len(values)):
        if i < period:
            result.append(None)
            continue

        window = values[i - period:i]
        mean = float(np.mean(window))
        std = float(np.std(window))

        if std == 0:
            result.append(0.0)
        else:
            result.append((values[i] - mean) / std)

    return result


def volatility(values: List[float], period: int = 20) -> List[Optional[float]]:
    """Rolling standard deviation."""
    result: List[Optional[float]] = []

    for i in range(len(values)):
        if i < period:
            result.append(None)
            continue

        window = values[i - period:i]
        result.append(float(np.std(window)))

    return result


def atr(
    high: List[float],
    low: List[float],
    close: List[float],
    period: int = 14,
) -> List[Optional[float]]:
    """Average True Range."""
    if len(close) < 2:
        return [None] * len(close)

    tr_vals: List[float] = [high[0] - low[0]]

    for i in range(1, len(close)):
        tr = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
        tr_vals.append(tr)

    result: List[Optional[float]] = [None] * (period - 1)
    atr_val = float(np.mean(tr_vals[:period]))
    result.append(atr_val)

    for i in range(period, len(tr_vals)):
        atr_val = (atr_val * (period - 1) + tr_vals[i]) / period
        result.append(atr_val)

    return result


def stochastic(
    high: List[float],
    low: List[float],
    close: List[float],
    k_period: int = 14,
    d_period: int = 3,
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Stochastic Oscillator (%K and %D)."""
    k_vals: List[Optional[float]] = []

    for i in range(len(close)):
        if i < k_period - 1:
            k_vals.append(None)
            continue

        h_max = max(high[i - k_period + 1:i + 1])
        l_min = min(low[i - k_period + 1:i + 1])
        denom = h_max - l_min

        if denom == 0:
            k_vals.append(50.0)
        else:
            k_vals.append((close[i] - l_min) / denom * 100)

    # Compute %D using SMA of %K
    k_clean = [v if v is not None else 0.0 for v in k_vals]
    d_raw = sma(k_clean, d_period)

    # Align %D with %K
    first_valid = next(
        (idx for idx, val in enumerate(k_vals) if val is not None),
        len(k_vals),
    )

    d_vals: List[Optional[float]] = [None] * min(
        first_valid + d_period - 1,
        len(d_raw),
    )
    d_vals.extend(d_raw[len(d_vals):])

    return k_vals, d_vals


def linear_regression_prediction(values: List[float]) -> Optional[float]:
    """Predict next value using linear regression."""
    if len(values) < 10:
        return None

    x_vals = np.arange(len(values))
    y_vals = np.array(values)

    matrix = np.vstack([x_vals, np.ones(len(x_vals))]).T
    slope, intercept = np.linalg.lstsq(matrix, y_vals, rcond=None)[0]

    return float(slope * len(values) + intercept)


def analyze_series(
    data: List[Tuple[str, float]],
    highs: Optional[List[float]] = None,
    lows: Optional[List[float]] = None,
) -> dict:
    """Return latest indicator values for a (timestamp, price) series.

    The Stochastic Oscillator is defined on intraday highs and lows. When
    only a close-price series is available (the real-time tracker stores
    closes), ``highs``/``lows`` are omitted and ``stoch_k``/``stoch_d`` are
    returned as ``None`` rather than fabricated from the close price — feeding
    the close in as both high and low produces a degenerate, incorrect value.
    """
    if not data:
        return {}

    prices = [price for _, price in data]

    sma20_vals = sma(prices, 20)
    sma50_vals = sma(prices, 50)
    ema20_vals = ema(prices, 20)
    rsi_vals = rsi(prices, 14)
    vol_vals = volatility(prices, 20)
    z_vals = zscore(prices, 20)
    prediction = linear_regression_prediction(prices)

    stoch_k: Optional[float] = None
    stoch_d: Optional[float] = None
    if highs is not None and lows is not None and len(highs) == len(lows) == len(prices):
        k_vals, d_vals = stochastic(highs, lows, prices)
        stoch_k = k_vals[-1]
        stoch_d = d_vals[-1]

    return {
        "sma20": sma20_vals[-1],
        "sma50": sma50_vals[-1],
        "ema20": ema20_vals[-1],
        "rsi14": rsi_vals[-1],
        "vol20": vol_vals[-1],
        "z_score": z_vals[-1],
        "prediction_next": prediction,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
    }
