"""
equity_gex_core.py — GEX for ANY CBOE underlying (SPY/QQQ/IWM/NVDA/TSLA/…).
================================================================================
Pure-logic module (no Streamlit side effects). gex_core.compute_symbol is wired
to the ES/NQ/GC futures map; this generalises the exact same, already-validated
math (dollar-gamma aggregation, true zero-gamma root-find, wall extraction,
expected-move band, trade plan) to any optionable ticker on the CBOE CDN — index
products (SPX/NDX/RUT) and equities/ETFs alike.

It deliberately REUSES gex_core's functions rather than re-deriving them, so the
numbers are identical in spirit to the futures radar and there is one source of
truth for the formulas. See gex_core's header for the math and the honest data
caveats (CBOE is ~15-min delayed; OI updates after the close).

DIFFERENCES vs the futures path
-------------------------------
* Spot comes straight from the CBOE payload (the underlying's own price), so no
  yfinance anchor / GC-style scaling is needed: scale = 1.
* Dividend yield q defaults to 0. For single stocks the intraday gamma effect of
  q is negligible; for high-yield ETFs you can pass a q to be precise.

HONEST LIMITATIONS
------------------
* Same retail-flow dealer-sign assumption as gex_core (long calls / short puts).
  It is well-validated for big index/ETF flow; on heavily retail single names
  (meme stocks) the sign can flip and the walls mislead — read the gex_core note.
* Thin single-name chains can leave no strike near a 1σ move or no zero-gamma
  root nearby; those fields then come back None rather than guessing.
"""

from __future__ import annotations

from datetime import date as _date, datetime as _datetime

import pandas as pd

import gex_core as gx

# Index products need the CDN underscore prefix; everything else is an equity/ETF.
_INDEX = {"SPX", "NDX", "RUT", "XSP", "VIX", "DJX", "MXEF"}

# Handy presets for the UI (not a restriction — any ticker works).
PRESETS = ["SPY", "QQQ", "IWM", "DIA", "NVDA", "TSLA", "AAPL", "AMD", "META",
           "AMZN", "MSFT", "GOOGL", "NFLX", "SPX", "NDX", "RUT"]


def _is_index(sym: str) -> bool:
    return sym.upper() in _INDEX


def _weighted_avg_dte(df_use: pd.DataFrame) -> int:
    """Gamma-×-OI weighted average DTE across a multi-expiry book (>= 1)."""
    if df_use is None or df_use.empty:
        return 1
    today = _date.today()
    num = den = 0.0
    for _, r in df_use.iterrows():
        try:
            d = _datetime.strptime(r["exp"], "%Y-%m-%d").date()
            dte = max((d - today).days, 0)
        except Exception:
            continue
        w = float(r.get("gamma", 0) or 0) * float(r.get("oi", 0) or 0)
        if w <= 0 or dte <= 0:
            continue
        num += dte * w
        den += w
    return max(1, int(round(num / den))) if den > 0 else 1


def compute(ticker: str, horizon: str = "daytrade", strike_range: float = 0.10,
            q: float = 0.0, now: _datetime | None = None) -> dict:
    """
    Full GEX read for one underlying.

    horizon = "daytrade" → nearest expiry (0DTE if today, else ≤5 DTE, else front).
              "swing"    → the 6 nearest expiries aggregated (HAG-style book).
    Returns a flat dict mirroring gex_core.compute_symbol's keys, plus `agg`
    (per-strike exposure frame) for charting. Best-effort: never raises.
    """
    sym = ticker.upper().strip()
    is_idx = _is_index(sym)
    try:
        raw = gx.fetch_cboe_raw(sym, is_idx)
        data = raw.get("data", {})
        spot = float(data.get("current_price") or data.get("close") or 0)
        if not spot:
            return {"symbol": sym, "error": "no spot from CBOE (bad ticker?)"}
        opts = data.get("options", [])
        if not opts:
            return {"symbol": sym, "error": "no options from CBOE (not optionable?)"}

        df = gx.parse_chain(opts, spot, scale=1.0, strike_range=strike_range, q=q)
        if df.empty:
            return {"symbol": sym, "error": "no valid strikes after filter"}

        if horizon == "swing":
            exps = sorted(df["exp"].unique())[:6]
            df_use = df[df["exp"].isin(exps)].copy()
            exp_used = exps[0] if exps else ""
            dte = _weighted_avg_dte(df_use)        # band horizon = book's avg DTE
        else:
            df_use, exp_used, dte = gx._pick_daytrade_expiry(df)
        if df_use.empty:
            return {"symbol": sym, "error": "no tradeable expiry"}

        agg = gx.aggregate_exposures(df_use, spot, q=q)
        walls = gx._wall_strikes(agg, spot)
        flip = gx.zero_gamma_spot(df_use, spot, q=q)
        net_total = float(agg["net_gex"].sum()) if not agg.empty else 0.0
        sig, upper, lower = gx.expected_move(df_use, spot,
                                             dte if isinstance(dte, int) else 1, now=now)
        plan = gx.build_plan(spot, flip, walls["call_wall"], walls["put_wall"], net_total)

        return {
            "symbol": sym,
            "is_index": is_idx,
            "spot": round(spot, 2),
            "horizon": horizon,
            "expiry": exp_used,
            "dte": dte if isinstance(dte, int) else None,
            "call_wall": walls["call_wall"],
            "put_wall": walls["put_wall"],
            "secondary_call_wall": walls["secondary_call_wall"],
            "secondary_put_wall": walls["secondary_put_wall"],
            "gamma_flip": round(flip, 2) if flip is not None else None,
            "net_gex": net_total,
            "sigma": sig,
            "upper_1sigma": upper,
            "lower_1sigma": lower,
            "regime": plan["regime"],
            "bias": plan["bias"],
            "playbook": plan["playbook"],
            "agg": agg,
            "expiries": sorted(df["exp"].unique()),
        }
    except Exception as exc:
        return {"symbol": sym, "error": str(exc)}
