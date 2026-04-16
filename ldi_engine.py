"""
ldi_engine.py
-------------
Core LDI (Liability-Driven Investment) math engine.

Computes:
  - Liability NPV and PV01 via DCF (30-year USD pension, $15M × 1.025^t)
  - Asset PV01 per $1M notional for US Treasuries and USD Swaps
  - Portfolio optimization via per-bucket KRD matching (±5% tolerance)
  - PV01 bucket breakdown (Short / Intermediate / Long)
  - Yield curve and cashflow data for charting

No external solvers required — pure Python + NumPy + pandas.
"""

import math
import numpy as np
import pandas as pd

CALC_YEARS      = 30
BASE_RATE       = 0.0445   # Flat USD discount curve
BUCKET_TOLERANCE = 0.05    # ±5% tolerance per KRD bucket

BUCKET_DEFS = [
    {"name": "Short",        "min_tenor": 0,  "max_tenor": 5,   "liab_share": 0.10},
    {"name": "Intermediate", "min_tenor": 5,  "max_tenor": 15,  "liab_share": 0.30},
    {"name": "Long",         "min_tenor": 15, "max_tenor": 100, "liab_share": 0.60},
]

BUCKET_LABELS = {
    "Short":        "Short (0-5Y)",
    "Intermediate": "Intermediate (5-15Y)",
    "Long":         "Long (15-30Y+)",
}


def _discount_factor(rate: float, years: float) -> float:
    """Continuous compounding discount factor."""
    return math.exp(-rate * years)


def calculate_liabilities(shock_bps: int = 0) -> dict:
    """Calculate liability NPV and PV01 for a 30-year USD pension."""
    shock = shock_bps / 10_000
    rate_base = BASE_RATE + shock
    rate_up   = rate_base + 0.0001  # +1bp

    payouts = [15_000_000 * (1.025 ** t) for t in range(1, CALC_YEARS + 1)]

    npv_base = sum(cf * _discount_factor(rate_base, t) for t, cf in enumerate(payouts, 1))
    npv_up   = sum(cf * _discount_factor(rate_up,   t) for t, cf in enumerate(payouts, 1))

    pv01 = abs(npv_base - npv_up)
    return {"npv": npv_base, "pv01": pv01}


def get_assets(shock_bps: int = 0) -> pd.DataFrame:
    """Build the asset universe with PV01 per $1M notional."""
    shock = shock_bps / 10_000
    raw = [
        {"Instrument": "UST 2Y",       "Tenor_Yrs": 2,  "Yield": 0.0465, "Type": "Treasury"},
        {"Instrument": "UST 5Y",       "Tenor_Yrs": 5,  "Yield": 0.0435, "Type": "Treasury"},
        {"Instrument": "UST 10Y",      "Tenor_Yrs": 10, "Yield": 0.0440, "Type": "Treasury"},
        {"Instrument": "UST 30Y",      "Tenor_Yrs": 30, "Yield": 0.0455, "Type": "Treasury"},
        {"Instrument": "USD Swap 10Y", "Tenor_Yrs": 10, "Yield": 0.0425, "Type": "Swap"},
        {"Instrument": "USD Swap 30Y", "Tenor_Yrs": 30, "Yield": 0.0410, "Type": "Swap"},
    ]
    df = pd.DataFrame(raw)
    df["Price"]        = 1_000_000
    df["Mod_Duration"] = df["Tenor_Yrs"] / (1 + df["Yield"] + shock)
    df["PV01_per_1M"]  = df["Price"] * df["Mod_Duration"] * 0.0001
    return df


def optimize_portfolio(assets: pd.DataFrame, target_pv01: float, budget_m: float) -> pd.DataFrame | None:
    """KRD-constrained portfolio optimizer (no Gurobi required)."""
    df = assets.copy().reset_index(drop=True)
    w  = np.zeros(len(df))
    total_notional = 0.0

    for bucket in BUCKET_DEFS:
        liab_bucket_pv01 = target_pv01 * bucket["liab_share"]
        pv01_min = liab_bucket_pv01 * (1 - BUCKET_TOLERANCE)
        pv01_max = liab_bucket_pv01 * (1 + BUCKET_TOLERANCE)

        in_bucket = df[
            (df["Tenor_Yrs"] > bucket["min_tenor"]) &
            (df["Tenor_Yrs"] <= bucket["max_tenor"])
        ].sort_values("PV01_per_1M", ascending=False)

        if in_bucket.empty:
            continue

        bucket_pv01 = 0.0

        for idx in in_bucket.index:
            if bucket_pv01 >= pv01_min:
                break
            needed       = pv01_min - bucket_pv01
            units_needed = needed / df.at[idx, "PV01_per_1M"]
            budget_left  = budget_m - total_notional
            actual       = min(units_needed, budget_left)
            if actual <= 0:
                continue
            w[idx]        += actual
            bucket_pv01   += actual * df.at[idx, "PV01_per_1M"]
            total_notional += actual

        if bucket_pv01 < pv01_min:
            return None  # Infeasible

        if bucket_pv01 > pv01_max:
            last_idx  = in_bucket.index[-1]
            excess    = bucket_pv01 - pv01_max
            reduction = excess / df.at[last_idx, "PV01_per_1M"]
            w[last_idx]     = max(0.0, w[last_idx] - reduction)
            total_notional -= reduction

    df["Optimal_Notional_M"] = w
    df["Allocated_PV01"]     = w * df["PV01_per_1M"]

    result = df[df["Optimal_Notional_M"] > 0.01].copy()
    return result if not result.empty else None


def get_pv01_buckets(liab_pv01: float, portfolio: pd.DataFrame) -> pd.DataFrame:
    """Return per-bucket PV01 comparison with tolerance bands."""
    rows = []
    for b in BUCKET_DEFS:
        liab_bucket = liab_pv01 * b["liab_share"]
        hedged = portfolio[
            (portfolio["Tenor_Yrs"] > b["min_tenor"]) &
            (portfolio["Tenor_Yrs"] <= b["max_tenor"])
        ]["Allocated_PV01"].sum()
        tol_low  = liab_bucket * (1 - BUCKET_TOLERANCE)
        tol_high = liab_bucket * (1 + BUCKET_TOLERANCE)
        within   = tol_low <= hedged <= tol_high
        deviation = ((hedged - liab_bucket) / liab_bucket * 100) if liab_bucket else -100
        rows.append({
            "Bucket":        BUCKET_LABELS[b["name"]],
            "Liability_PV01": liab_bucket,
            "Hedged_PV01":   hedged,
            "Tol_Low":       tol_low,
            "Tol_High":      tol_high,
            "Within_Tol":    within,
            "Deviation_Pct": deviation,
        })
    return pd.DataFrame(rows)


def get_yield_curve(shock_bps: int = 0) -> pd.DataFrame:
    """Return US Treasury yield curve data with applied shock."""
    shock = shock_bps / 10_000
    data = [
        {"Tenor": "2Y",  "Yield_Pct": (0.0465 + shock) * 100},
        {"Tenor": "5Y",  "Yield_Pct": (0.0435 + shock) * 100},
        {"Tenor": "10Y", "Yield_Pct": (0.0440 + shock) * 100},
        {"Tenor": "20Y", "Yield_Pct": (0.0450 + shock) * 100},
        {"Tenor": "30Y", "Yield_Pct": (0.0455 + shock) * 100},
    ]
    return pd.DataFrame(data)


def get_liability_cashflows() -> pd.DataFrame:
    """Return the 30-year liability cashflow and PV schedule."""
    rows = []
    for t in range(1, CALC_YEARS + 1):
        cf = 15_000_000 * (1.025 ** t)
        pv = cf * _discount_factor(BASE_RATE, t)
        rows.append({"Year": t, "Cashflow_M": cf / 1e6, "PV_M": pv / 1e6})
    return pd.DataFrame(rows)


def sensitivity_table(shock_range: list[int] | None = None) -> pd.DataFrame:
    """Compute liability NPV and PV01 across a range of yield shocks."""
    if shock_range is None:
        shock_range = list(range(-100, 125, 25))
    base = calculate_liabilities(0)
    rows = []
    for shock in shock_range:
        r = calculate_liabilities(shock)
        rows.append({
            "Shock (bps)":   shock,
            "Liability NPV": r["npv"],
            "Liability PV01": r["pv01"],
            "NPV Change ($M)": (r["npv"] - base["npv"]) / 1e6,
        })
    return pd.DataFrame(rows)
