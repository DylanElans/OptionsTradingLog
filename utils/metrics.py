from __future__ import annotations

import math
from typing import Dict

import pandas as pd

from utils.enums import CREDIT_ACTIONS


def safe_float(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        if isinstance(x, float) and math.isnan(x):
            return default
        return float(x)
    except Exception:
        return default



def safe_int(x, default=0):
    try:
        if x is None or x == "":
            return default
        return int(x)
    except Exception:
        return default



def none_if_zero(x):
    value = safe_float(x, 0.0)
    return None if value == 0 else value



def normalize_text(v: str | None) -> str | None:
    if v is None:
        return None
    out = str(v).strip()
    return out or None



def compute_gross_amount(premium: float, qty: int, multiplier: int) -> float:
    return safe_float(premium, 0.0) * safe_int(qty, 0) * safe_int(multiplier, 0)



def compute_net_cash_flow(action: str, premium: float, qty: int, multiplier: int, fees: float) -> float:
    gross = compute_gross_amount(premium, qty, multiplier)
    sign = 1 if (action or "").upper() in CREDIT_ACTIONS else -1
    return sign * gross - safe_float(fees, 0.0)



def infer_break_even(option_strategy: str, option_right: str, strike_price: float | None, premium: float | None, share_price_at_trans: float | None) -> float | None:
    strategy = (option_strategy or "").upper()
    right = (option_right or "").upper()
    strike = None if strike_price is None else safe_float(strike_price, None)
    prem = safe_float(premium, 0.0)

    if strategy == "CSP" and strike is not None:
        return strike - prem
    if strategy == "CC" and strike is not None:
        return strike + prem
    if strategy == "STOCK":
        return safe_float(share_price_at_trans, None)
    if strategy == "PUT_SPREAD":
        return None
    if strategy == "CALL_SPREAD":
        return None
    if strategy in {"COLLAR", "SYNTHETIC_SHORT", "SYNTHETIC_LONG"}:
        return safe_float(share_price_at_trans, None)
    if right == "PUT" and strike is not None:
        return strike - prem
    if right == "CALL" and strike is not None:
        return strike + prem
    return safe_float(share_price_at_trans, None)



def compute_annualized_return(closed_pnl: float, capital_at_risk: float, holding_days: int) -> float | None:
    pnl = safe_float(closed_pnl, 0.0)
    risk = safe_float(capital_at_risk, 0.0)
    days = safe_int(holding_days, 0)
    if risk <= 0 or days <= 0:
        return None
    return (pnl / risk) * (365 / days)



def summarize_trades(df: pd.DataFrame) -> Dict[str, float]:
    if df.empty:
        return {
            "trade_count": 0,
            "open_count": 0,
            "closed_count": 0,
            "win_rate": 0.0,
            "total_closed_pnl": 0.0,
            "avg_closed_pnl": 0.0,
            "total_fees": 0.0,
            "avg_iv_rank": 0.0,
            "avg_annualized_return": 0.0,
        }

    work = df.copy()
    work["result_status"] = work["result_status"].fillna("OPEN").str.upper()
    closed = work[work["result_status"].isin(["CLOSED", "EXPIRED", "ASSIGNED"])]
    wins = closed[pd.to_numeric(closed["closed_pnl"], errors="coerce").fillna(0) > 0]

    return {
        "trade_count": int(len(work)),
        "open_count": int((~work["result_status"].isin(["CLOSED", "EXPIRED", "ASSIGNED"])).sum()),
        "closed_count": int(len(closed)),
        "win_rate": float(len(wins) / len(closed) * 100) if len(closed) else 0.0,
        "total_closed_pnl": float(pd.to_numeric(closed["closed_pnl"], errors="coerce").fillna(0).sum()),
        "avg_closed_pnl": float(pd.to_numeric(closed["closed_pnl"], errors="coerce").fillna(0).mean()) if len(closed) else 0.0,
        "total_fees": float(pd.to_numeric(work["fees"], errors="coerce").fillna(0).sum()),
        "avg_iv_rank": float(pd.to_numeric(work["iv_rank"], errors="coerce").fillna(0).mean()) if len(work) else 0.0,
        "avg_annualized_return": float(pd.to_numeric(closed["annualized_return"], errors="coerce").dropna().mean()) if len(closed) else 0.0,
    }



def build_equity_curve(df: pd.DataFrame, ledger_df: pd.DataFrame | None = None) -> pd.DataFrame:
    pnl_daily = pd.DataFrame(columns=["date", "delta"])
    if not df.empty:
        work = df.copy()
        work["event_date"] = pd.to_datetime(work["closed_date"].fillna(work["trade_date"]), errors="coerce")
        work["delta"] = pd.to_numeric(work["closed_pnl"], errors="coerce").fillna(0.0)
        pnl_daily = work.groupby(work["event_date"].dt.date, dropna=True)["delta"].sum().reset_index()
        pnl_daily.columns = ["date", "delta"]

    cash_daily = pd.DataFrame(columns=["date", "delta"])
    if ledger_df is not None and not ledger_df.empty:
        cash = ledger_df.copy()
        cash["ledger_date"] = pd.to_datetime(cash["ledger_date"], errors="coerce")
        cash["delta"] = pd.to_numeric(cash["amount"], errors="coerce").fillna(0.0)
        cash_daily = cash.groupby(cash["ledger_date"].dt.date, dropna=True)["delta"].sum().reset_index()
        cash_daily.columns = ["date", "delta"]

    if pnl_daily.empty and cash_daily.empty:
        return pd.DataFrame(columns=["date", "equity"])

    combined = pd.concat([pnl_daily, cash_daily], ignore_index=True)
    combined = combined.groupby("date", as_index=False)["delta"].sum().sort_values("date")
    combined["equity"] = combined["delta"].cumsum()
    return combined[["date", "equity"]]



def build_lightweight_series(curve_df: pd.DataFrame):
    if curve_df.empty:
        return []
    return [{
        "chart": {"height": 380},
        "series": [{
            "type": "Line",
            "data": [
                {"time": str(row["date"]), "value": float(row["equity"])} for _, row in curve_df.iterrows()
            ],
            "options": {"lineWidth": 2},
        }],
    }]
