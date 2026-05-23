"""
timeseries_analyzer.py
=======================
Analyses a time-series CSV and returns a JSON-serialisable pattern model.

Pattern model shape
-------------------
{
  "time_column": "date",
  "inferred_frequency": "D",          # pandas offset alias
  "source_start": "2013-01-01",
  "source_end":   "2017-01-01",
  "source_rows":  1462,
  "columns": {
    "meantemp": {
      "trend_slope":          0.0003,
      "trend_intercept":      18.5,
      "seasonal_period":      365,     # dominant STL period (integer samples)
      "seasonal_amplitudes":  [8.2, 1.1],
      "seasonal_phases":      [1.57, 0.0],
      "residual_mean":        0.0,
      "residual_std":         2.1,
      "autocorr_lag1":        0.87,
      "value_min":            6.0,
      "value_max":            38.9
    },
    ...
  },
  "residual_correlation_matrix": {"meantemp": {"humidity": -0.72, ...}, ...}
}
"""

from __future__ import annotations

import json
import warnings
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# statsmodels import — graceful fallback if not yet installed in the running
# process (pip install is triggered separately before analysis is called)
try:
    from statsmodels.tsa.seasonal import STL  # type: ignore
    _HAS_STL = True
except ImportError:
    _HAS_STL = False

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

FREQ_ALIASES = {
    "D": "D",
    "W": "W",
    "M": "MS",   # month-start
    "H": "h",
    "T": "min",
    "min": "min",
    "h": "h",
    "MS": "MS",
    "QS": "QS",
    "YS": "YS",
}

_FREQ_PERIOD_MAP: Dict[str, int] = {
    "h": 24,
    "min": 1440,
    "D": 7,        # weekly seasonality in daily data – override by STL
    "W": 52,
    "MS": 12,
    "QS": 4,
    "YS": 1,
}

_FREQ_DISPLAY: Dict[str, str] = {
    "h": "Hourly",
    "min": "Minutely",
    "D": "Daily",
    "W": "Weekly",
    "MS": "Monthly",
    "QS": "Quarterly",
    "YS": "Yearly",
}


def infer_frequency(dt_series: pd.Series) -> str:
    """Best-guess pandas offset alias for an irregular or regular datetime column."""
    if len(dt_series) < 2:
        return "D"
    sorted_s = dt_series.dropna().sort_values().reset_index(drop=True)
    diffs = sorted_s.diff().dropna()
    median_diff = diffs.median()
    total_seconds = median_diff.total_seconds()
    if total_seconds <= 0:
        return "D"
    if total_seconds < 3600:
        return "min"
    if total_seconds < 86400:
        return "h"
    if total_seconds < 86400 * 6:
        return "D"
    if total_seconds < 86400 * 20:
        return "W"
    if total_seconds < 86400 * 60:
        return "MS"
    if total_seconds < 86400 * 100:
        return "QS"
    return "YS"


def _dominant_period_for_freq(freq: str, n_samples: int) -> int:
    """Return the most meaningful seasonal period (in samples) for the given frequency."""
    if freq == "D":
        # Prefer annual (365) if we have at least 2 years of data
        if n_samples >= 730:
            return 365
        if n_samples >= 52:
            return 7
        return max(2, n_samples // 4)
    if freq == "h":
        return 24
    if freq == "min":
        return 1440
    if freq == "W":
        return 52 if n_samples >= 104 else max(2, n_samples // 4)
    if freq in ("MS", "QS"):
        return 12 if n_samples >= 24 else max(2, n_samples // 2)
    return max(2, n_samples // 4)


def _stl_decompose(series: pd.Series, period: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Run STL decomposition. Returns (trend, seasonal, residual) as numpy arrays.
    Falls back to simple additive decomposition when statsmodels is unavailable.
    """
    values = series.ffill().bfill().values.astype(float)
    n = len(values)
    period = max(2, min(period, n // 2))  # guard

    if _HAS_STL and n >= 2 * period:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                stl = STL(values, period=period, robust=True)
                res = stl.fit()
            return res.trend, res.seasonal, res.resid
        except Exception:
            pass  # fall through to numpy path

    # Numpy fallback: moving-average trend + sinusoidal seasonal estimate
    window = min(period, n)
    trend = np.convolve(values, np.ones(window) / window, mode="same")
    detrended = values - trend
    # Mean seasonal pattern over each phase position
    seasonal = np.zeros(n)
    for i in range(period):
        idxs = list(range(i, n, period))
        avg = float(np.nanmean(detrended[idxs]))
        for idx in idxs:
            seasonal[idx] = avg
    residual = values - trend - seasonal
    return trend, seasonal, residual


def _safe_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
        if np.isfinite(v):
            return round(v, 6)
    except Exception:
        pass
    return None


def _seasonal_to_amplitude_phase(seasonal: np.ndarray, period: int) -> Tuple[List[float], List[float]]:
    """
    Convert a seasonal component (already extracted by STL) into amplitude/phase
    pairs for the dominant harmonics via FFT.
    """
    if len(seasonal) < period:
        return [float(np.std(seasonal))], [0.0]

    fft_vals = np.fft.rfft(seasonal)
    amplitudes = np.abs(fft_vals) * 2 / len(seasonal)
    phases = np.angle(fft_vals)
    freqs = np.fft.rfftfreq(len(seasonal))

    # Keep top-2 harmonics (excluding DC component at index 0)
    sorted_idx = np.argsort(amplitudes[1:])[::-1] + 1
    top = sorted_idx[:2]
    amps = [round(float(amplitudes[i]), 6) for i in top if amplitudes[i] > 1e-6]
    phs = [round(float(phases[i]), 6) for i in top if amplitudes[i] > 1e-6]
    if not amps:
        amps = [round(float(np.std(seasonal)), 6)]
        phs = [0.0]
    return amps, phs


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------

def analyze(
    file_path: str,
    time_column: str,
    max_rows: int = 50_000,
) -> Dict[str, Any]:
    """
    Analyse the CSV at *file_path* treating *time_column* as the time index.

    Returns a pattern model dict (JSON-serialisable).
    Raises ValueError if the time column cannot be parsed or the file is invalid.
    """
    # 1. Load data
    df = pd.read_csv(file_path, nrows=max_rows)
    if time_column not in df.columns:
        raise ValueError(f"Column '{time_column}' not found in CSV.")

    # 2. Parse datetime column
    try:
        df[time_column] = pd.to_datetime(df[time_column])
    except Exception as exc:
        raise ValueError(f"Cannot parse '{time_column}' as datetime: {exc}") from exc

    df = df.sort_values(time_column).reset_index(drop=True)
    n = len(df)
    if n < 4:
        raise ValueError("Need at least 4 rows to analyse time-series patterns.")

    # 3. Infer frequency
    freq = infer_frequency(df[time_column])
    period = _dominant_period_for_freq(freq, n)

    source_start = str(df[time_column].iloc[0].date())
    source_end = str(df[time_column].iloc[-1].date())

    # 4. Numeric columns only (exclude the time column)
    numeric_cols = [
        c for c in df.columns
        if c != time_column and pd.api.types.is_numeric_dtype(df[c])
    ]

    time_index = np.arange(n, dtype=float)

    column_models: Dict[str, Any] = {}
    residuals_matrix: Dict[str, np.ndarray] = {}

    for col in numeric_cols:
        series = df[col].ffill().bfill()
        values = series.values.astype(float)

        # 4a. Trend via linear regression on time index
        coeffs = np.polyfit(time_index, values, 1)
        slope = _safe_float(coeffs[0]) or 0.0
        intercept = _safe_float(coeffs[1]) or 0.0
        trend_line = np.polyval(coeffs, time_index)

        # 4b. STL seasonal decomposition on detrended series
        detrended_series = pd.Series(values - trend_line)
        _, seasonal, residual = _stl_decompose(detrended_series, period)

        # 4c. Seasonal amplitude / phase
        amplitudes, phases = _seasonal_to_amplitude_phase(seasonal, period)

        # 4d. Residual stats
        res_mean = _safe_float(float(np.mean(residual))) or 0.0
        res_std = _safe_float(float(np.std(residual))) or 0.001

        # 4e. Autocorrelation at lag-1 (with safety for short series)
        s_for_autocorr = pd.Series(residual)
        try:
            autocorr_lag1 = _safe_float(s_for_autocorr.autocorr(lag=1)) or 0.0
        except Exception:
            autocorr_lag1 = 0.0

        # 4f. Observed bounds
        val_min = _safe_float(float(np.nanmin(values))) or 0.0
        val_max = _safe_float(float(np.nanmax(values))) or 1.0

        column_models[col] = {
            "trend_slope": slope,
            "trend_intercept": intercept,
            "seasonal_period": int(period),
            "seasonal_amplitudes": amplitudes,
            "seasonal_phases": phases,
            "residual_mean": res_mean,
            "residual_std": res_std,
            "autocorr_lag1": autocorr_lag1,
            "value_min": val_min,
            "value_max": val_max,
        }
        residuals_matrix[col] = residual

    # 5. Cross-column correlation matrix (on residuals)
    corr_matrix: Dict[str, Dict[str, float]] = {}
    if len(numeric_cols) >= 2:
        res_df = pd.DataFrame({c: residuals_matrix[c] for c in numeric_cols})
        corr_df = res_df.corr()
        for c1 in numeric_cols:
            corr_matrix[c1] = {}
            for c2 in numeric_cols:
                v = _safe_float(corr_df.loc[c1, c2])
                corr_matrix[c1][c2] = v if v is not None else 0.0

    return {
        "time_column": time_column,
        "inferred_frequency": freq,
        "frequency_label": _FREQ_DISPLAY.get(freq, freq),
        "source_start": source_start,
        "source_end": source_end,
        "source_rows": int(n),
        "seasonal_period": int(period),
        "numeric_columns": numeric_cols,
        "columns": column_models,
        "residual_correlation_matrix": corr_matrix,
    }


def detect_time_columns(file_path: str, max_rows: int = 500) -> List[str]:
    """
    Heuristic scan of a CSV to find columns that look like datetime.
    Returns a list of candidate column names, best guess first.
    """
    try:
        df = pd.read_csv(file_path, nrows=max_rows)
    except Exception:
        return []

    candidates: List[str] = []
    for col in df.columns:
        # Already a datetime dtype
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            candidates.insert(0, col)
            continue
        # Try parsing a sample
        sample = df[col].dropna().head(10)
        if sample.empty:
            continue
        # Name heuristic
        col_lower = col.lower()
        is_name_hint = any(
            token in col_lower
            for token in ["date", "time", "dt", "timestamp", "day", "month", "year", "ts"]
        )
        try:
            pd.to_datetime(sample)
            if is_name_hint:
                candidates.insert(0, col)
            else:
                candidates.append(col)
        except Exception:
            pass

    return candidates


def model_summary_text(model: Dict[str, Any]) -> str:
    """Return a short human-readable summary of a pattern model."""
    freq_label = model.get("frequency_label", model.get("inferred_frequency", "?"))
    n_cols = len(model.get("numeric_columns", []))
    period = model.get("seasonal_period", "?")
    rows = model.get("source_rows", "?")
    start = model.get("source_start", "?")
    end = model.get("source_end", "?")
    return (
        f"{freq_label} data · {rows} rows · {start} → {end} · "
        f"{n_cols} numeric column(s) · seasonal period ≈ {period} steps"
    )
