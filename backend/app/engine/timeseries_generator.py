"""
timeseries_generator.py
=======================
Generates a synthetic time-series DataFrame from a pattern model produced
by timeseries_analyzer.analyze().

Algorithm per numeric column
-----------------------------
value(t) = trend(t) + seasonal(t) + correlated_AR1_residual(t)

  trend(t)    = slope * t + intercept
  seasonal(t) = sum_k( A_k * sin(2π·t / P + φ_k) )   [re-synthesised from
                                                         amplitude/phase pairs]
  residual(t) = α · residual(t-1) + ε_t               [AR(1) process where
                α = autocorr_lag1, ε_t ~ MVN(0, Σ)]

Final values are clipped to [value_min, value_max] and the seasonal amplitude
can be scaled by the user-supplied `seasonal_scale` parameter.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_trend(n: int, slope: float, intercept: float) -> np.ndarray:
    t = np.arange(n, dtype=float)
    return slope * t + intercept


def _build_seasonal(
    n: int,
    amplitudes: List[float],
    phases: List[float],
    period: int,
    scale: float = 1.0,
) -> np.ndarray:
    """Re-synthesise the seasonal component from amplitude/phase pairs."""
    t = np.arange(n, dtype=float)
    seasonal = np.zeros(n)
    for amp, phase in zip(amplitudes, phases):
        seasonal += scale * amp * np.sin(2 * np.pi * t / max(period, 1) + phase)
    return seasonal


def _build_cov_matrix(
    columns: List[str],
    column_models: Dict[str, Any],
    corr_matrix: Dict[str, Dict[str, float]],
) -> np.ndarray:
    """
    Build a covariance matrix from residual std + correlation matrix.
    Falls back to identity if correlation data is missing / ill-conditioned.
    """
    k = len(columns)
    stds = np.array([column_models[c]["residual_std"] for c in columns], dtype=float)
    # Guard: replace zeros to avoid degenerate covariance
    stds = np.where(stds <= 0, 1e-6, stds)

    corr = np.eye(k)
    for i, c1 in enumerate(columns):
        for j, c2 in enumerate(columns):
            if i != j and corr_matrix and c1 in corr_matrix and c2 in corr_matrix.get(c1, {}):
                val = float(corr_matrix[c1].get(c2, 0.0))
                # Clamp correlation to avoid ill-conditioned matrix
                corr[i, j] = max(-0.99, min(0.99, val))

    cov = np.outer(stds, stds) * corr
    # Ensure positive semi-definiteness via nearest SPD
    try:
        np.linalg.cholesky(cov)
    except np.linalg.LinAlgError:
        # Nearest SPD: eigen-clamp
        eigvals, eigvecs = np.linalg.eigh(cov)
        eigvals = np.clip(eigvals, 1e-8, None)
        cov = eigvecs @ np.diag(eigvals) @ eigvecs.T
    return cov


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(
    pattern_model: Dict[str, Any],
    n_rows: int,
    start_date: Optional[str] = None,
    frequency: Optional[str] = None,
    seed: int = 42,
    seasonal_scale: float = 1.0,
) -> pd.DataFrame:
    """
    Generate a synthetic time-series DataFrame.

    Parameters
    ----------
    pattern_model   Pattern model dict from timeseries_analyzer.analyze().
    n_rows          Number of rows to generate.
    start_date      ISO date string for the first generated row.
                    Defaults to one frequency-step after source_end.
    frequency       Pandas offset alias override (e.g. 'D', 'W', 'MS').
                    Defaults to the inferred frequency in the model.
    seed            Random seed for reproducibility.
    seasonal_scale  Multiplier applied to seasonal amplitudes (0.5–2.0).
                    1.0 = reproduce source seasonality exactly.

    Returns
    -------
    pd.DataFrame with a time column (name taken from model) and one numeric
    column per column in the pattern model.
    """
    rng = np.random.default_rng(seed)

    time_column = pattern_model["time_column"]
    freq = frequency or pattern_model.get("inferred_frequency", "D")
    column_models: Dict[str, Any] = pattern_model.get("columns", {})
    corr_matrix: Dict[str, Dict[str, float]] = pattern_model.get("residual_correlation_matrix", {})
    numeric_cols = [c for c in pattern_model.get("numeric_columns", []) if c in column_models]

    if not numeric_cols:
        raise ValueError("Pattern model has no numeric columns to generate.")

    n_rows = max(1, int(n_rows))

    # ---------- Date range ----------
    if start_date:
        try:
            start_ts = pd.Timestamp(start_date)
        except Exception:
            start_ts = pd.Timestamp(pattern_model.get("source_end", "2020-01-01"))
            start_ts = start_ts + pd.tseries.frequencies.to_offset(freq)
    else:
        source_end = pattern_model.get("source_end", "2020-01-01")
        start_ts = pd.Timestamp(source_end) + pd.tseries.frequencies.to_offset(freq)

    date_range = pd.date_range(start=start_ts, periods=n_rows, freq=freq)

    # ---------- Covariance matrix for multi-variate residual noise ----------
    cov = _build_cov_matrix(numeric_cols, column_models, corr_matrix)

    # ---------- Generate correlated white noise (one row per time step) --------
    # Shape: (n_rows, k_columns)
    white_noise = rng.multivariate_normal(
        mean=np.zeros(len(numeric_cols)),
        cov=cov,
        size=n_rows,
    )

    # ---------- Build each column via trend + seasonal + AR(1) residual --------
    period = int(pattern_model.get("seasonal_period", 365))
    result: Dict[str, np.ndarray] = {}

    for col_idx, col in enumerate(numeric_cols):
        cm = column_models[col]
        slope = float(cm.get("trend_slope", 0.0))
        intercept = float(cm.get("trend_intercept", 0.0))
        amplitudes = [float(a) for a in cm.get("seasonal_amplitudes", [0.0])]
        phases = [float(p) for p in cm.get("seasonal_phases", [0.0])]
        res_mean = float(cm.get("residual_mean", 0.0))
        autocorr = float(cm.get("autocorr_lag1", 0.0))
        v_min = float(cm.get("value_min", -1e9))
        v_max = float(cm.get("value_max", 1e9))

        # Clamp autocorr to stable range
        autocorr = max(-0.99, min(0.99, autocorr))

        trend = _build_trend(n_rows, slope, intercept)
        seasonal = _build_seasonal(n_rows, amplitudes, phases, period, scale=seasonal_scale)

        # AR(1) residual with correlated white noise
        ar_residual = np.zeros(n_rows)
        ar_residual[0] = white_noise[0, col_idx] + res_mean
        for t in range(1, n_rows):
            ar_residual[t] = autocorr * ar_residual[t - 1] + white_noise[t, col_idx] + res_mean * (1 - autocorr)

        values = trend + seasonal + ar_residual

        # Clip to observed range
        values = np.clip(values, v_min, v_max)
        result[col] = values

    # ---------- Assemble DataFrame ----------
    df = pd.DataFrame({time_column: date_range})
    for col in numeric_cols:
        df[col] = np.round(result[col], 4)

    return df
