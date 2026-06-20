import streamlit as st
import pandas as pd
import requests
import builtins
import io
import sys
import os
import time
import math
import warnings
from datetime import date as _date, datetime as _datetime

warnings.filterwarnings('ignore')

import en_option_v3 as opt

# ─── GEX Radar — Futures Key Levels (ES / NQ / GC) ────────────────────────────
#
# Theory & research basis:
#   Bollen & Whaley (2004) J.Finance 59(2):711-754 — Dealer delta-hedging at
#     gamma-dense strikes creates mechanical price support/resistance (GEX walls).
#   Muravyev (2016) J.Finance 71(2):673-708 — Option order flow predicts
#     underlying returns via dealer hedge rebalancing flows.
#   Amin, Coval & Seyhun (2022) SSRN-4131538 — Zero-DTE options now dominate
#     intraday S&P flow; gamma-pinning to 0DTE strikes is systematic.
#   Carr & Wu (2016) — Systematic dealer hedging flows drive volatility regime.
#
# How it works:
#   GEX = Σ( OI × Gamma × 100 × Spot )  per strike, signed by call/put
#   under the SqueezeMetrics retail-flow assumption (dealers net short calls,
#   net short puts). Under that sign:
#     Call GEX > 0 at strike K → dealers long gamma at K → sell rallies /
#       buy dips → resistance (Call Wall) or support (Put Wall).
#     Net GEX > 0 (above Gamma Flip) → dealers stabilize price (mean-revert).
#     Net GEX < 0 (below Gamma Flip) → dealers amplify moves (trend).
#   HAG = Hedging Activity Gradient: GEX aggregated across all near expiries,
#     representing the full dealer book's "gravity field" on price.
#   0DTE = same-day expiry GEX; most violent because dealers hedge rapidly.
#
# CAVEAT: The retail-flow sign convention is empirically valid for SPX/NDX/SPY
# but can flip on (a) meme stocks where retail buys both sides aggressively
# and (b) 0DTE days where call/put taker mix flips intraday. For SPX/NDX/GLD
# (this radar's universe) the assumption is well-validated; for single-name
# extensions plug a CBOE COT or vol.land dealer-positioning feed.
# Modern flow vendors (Glassnode "Taker-Flow-Based GEX") infer dealer side
# from per-trade taker flags rather than assuming it.
#
# Data: CBOE free CDN (15-min delayed quotes, OI updates at EOD).
#   ES  ← SPX index options  (same price scale as ES futures)
#   NQ  ← NDX index options  (same price scale as NQ futures)
#   GC  ← GLD ETF options    (GLD ≈ gold/10 → scale ×10 to GC equivalent)

_FUTURES_CBOE = {
    "ES": ("SPX", True,  None),   # scale=None → SPX price = ES price directly
    "NQ": ("NDX", True,  None),   # scale=None → NDX price = NQ price directly
    "GC": ("GLD", False, None),   # scale=None → dynamically fetched GC=F / GLD ratio
}

# yfinance tickers used to get actual futures spot prices (for GC correction + validation)
_YF_FUTURES = {"ES": "ES=F", "NQ": "NQ=F", "GC": "GC=F"}

# Continuous dividend yield by underlying — used in q-adjusted Merton gamma
# and in the zero-gamma-spot root finder. Sources: S&P TTM yield (~1.3%),
# Nasdaq-100 TTM yield (~0.7%), GLD ETF (0% — gold pays no dividend).
_DIV_YIELD = {"ES": 0.013, "NQ": 0.007, "GC": 0.0}


@st.cache_data(ttl=120)
def _fetch_yf_spot(ticker: str) -> float | None:
    """Fetch latest close price from yfinance for a futures contract."""
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="2d")
        return float(h["Close"].iloc[-1]) if not h.empty else None
    except Exception:
        return None


def _bs_gamma_gex(S: float, K: float, T: float, sigma: float,
                  r: float = 0.05, q: float = 0.0) -> float:
    """
    Merton (1973) gamma with continuous dividend yield q:
        d1 = [ln(S/K) + (r − q + σ²/2)T] / (σ√T)
        Γ  = e^(−qT) × φ(d1) / (S σ √T)

    For SPX (q≈1.3%) and NDX (q≈0.7%) at <60 DTE the dividend
    correction is small (~0.1-0.6%) but it matters for LEAPS and
    keeps the formula consistent with what CBOE itself uses to
    publish its pre-computed γ values.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return (math.exp(-q * T) * math.exp(-0.5 * d1 ** 2)
                / (math.sqrt(2 * math.pi) * S * sigma * math.sqrt(T)))
    except Exception:
        return 0.0


def _fetch_cboe_gex_raw(sym: str, is_index: bool) -> dict:
    prefix = "_" if is_index else ""
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{prefix}{sym}.json"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _parse_options_gex(opts: list, spot_raw: float, scale: float,
                       strike_range: float = 0.20, q: float = 0.0) -> pd.DataFrame:
    today_str = _date.today().isoformat()
    rows = []
    for opt_rec in opts:
        code = opt_rec.get("option", "")
        try:
            i = next(j for j, c in enumerate(code) if c.isdigit())
            exp_str  = f"20{code[i:i+2]}-{code[i+2:i+4]}-{code[i+4:i+6]}"
            opt_type = code[i + 6]
            K_raw    = float(code[i + 7:]) / 1000.0
        except Exception:
            continue
        if abs(K_raw - spot_raw) / spot_raw > strike_range:
            continue
        oi    = float(opt_rec.get("open_interest") or 0)
        iv    = float(opt_rec.get("iv")            or 0)
        gamma = float(opt_rec.get("gamma")         or 0)
        if oi == 0:
            continue
        if gamma == 0 and iv > 0:
            exp_date = _datetime.strptime(exp_str, "%Y-%m-%d").date()
            days_to_exp = max((exp_date - _date.today()).days, 0)
            # 0DTE bugfix: at days=0 the naive T=0 breaks γ. Floor to actual
            # remaining intraday hours (assume 6.5h cash session). At market
            # open T ≈ 6.5/24/365 ≈ 7.4e-4; at close ≈ 1e-6. Use 1h as a
            # conservative floor for any 0DTE option we still see quotes on.
            min_T = 1.0 / 24.0 / 365.0   # 1 hour
            T = max(days_to_exp / 365.0, min_T)
            iv_dec = iv / 100.0 if iv > 1.0 else iv
            gamma  = _bs_gamma_gex(spot_raw, K_raw, T, iv_dec, q=q)
        if gamma == 0:
            continue
        rows.append({
            "strike":  round(K_raw * scale, 2),
            "type":    opt_type,
            "exp":     exp_str,
            "oi":      oi,
            "iv":      iv,
            "gamma":   gamma,
            "is_0dte": exp_str == today_str,
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _agg_gex_df(df: pd.DataFrame, spot: float) -> pd.DataFrame:
    rows = [{"strike": r["strike"],
             "gex": (1 if r["type"] == "C" else -1) * r["gamma"] * r["oi"] * 100 * spot}
            for _, r in df.iterrows()]
    if not rows:
        return pd.DataFrame()
    agg = (pd.DataFrame(rows)
           .groupby("strike")["gex"].sum()
           .reset_index()
           .sort_values("strike"))
    agg["cumgex"] = agg["gex"].cumsum()
    return agg


# ─── True Zero-Gamma-Spot  (SqueezeMetrics canonical) ─────────────────────────
# The simple cumulative-by-strike heuristic flags the strike at which running
# net γ flips sign at the CURRENT spot — it doesn't solve for the price that
# would make total dealer γ exactly zero. The proper SqueezeMetrics "Zero
# Gamma" level is the root of:
#       f(S*) = Σ_i sign_i × γ_i(S*) × OI_i × 100 × S*  = 0
# where γ_i is recomputed at the hypothetical spot S*. Below we use brentq on
# a ±15% window around current spot; if no sign change in that window we
# expand to ±30%/±40%, finally returning None if there's genuinely no flip
# (e.g. dealer book is unambiguously long or short gamma across all prices).

def _net_gex_at_spot(df_sub: pd.DataFrame, S_star: float, q: float = 0.0) -> float:
    today = _date.today()
    total = 0.0
    min_T = 1.0 / 24.0 / 365.0   # 1-hour floor for 0DTE (same as parse step)
    for _, row in df_sub.iterrows():
        try:
            exp_date = _datetime.strptime(row["exp"], "%Y-%m-%d").date()
            days = max((exp_date - today).days, 0)
            T = max(days / 365.0, min_T)
        except Exception:
            continue
        if T <= 0:
            continue
        iv     = row.get("iv", 0) or 0
        iv_dec = iv / 100.0 if iv > 1.0 else iv
        if iv_dec <= 0:
            continue
        gamma_star = _bs_gamma_gex(S_star, float(row["strike"]), T, iv_dec, q=q)
        if gamma_star == 0:
            continue
        sign = 1 if row["type"] == "C" else -1
        total += sign * gamma_star * row["oi"] * 100 * S_star
    return total


def _zero_gamma_spot(df_sub: pd.DataFrame, spot: float, q: float = 0.0) -> float | None:
    """
    Solve f(S*) = net dealer GEX at S* = 0  via Brent's method.
    Returns None when no sign change is found in ±40% of spot.
    """
    if df_sub is None or df_sub.empty:
        return None
    try:
        from scipy.optimize import brentq
        f = lambda s: _net_gex_at_spot(df_sub, s, q=q)
        for width in (0.15, 0.25, 0.40):
            lo, hi = spot * (1 - width), spot * (1 + width)
            flo, fhi = f(lo), f(hi)
            if flo == 0:
                return lo
            if fhi == 0:
                return hi
            if flo * fhi < 0:
                return float(brentq(f, lo, hi, xtol=max(spot * 0.0005, 0.01),
                                    maxiter=60))
        return None   # no flip in ±40% — dealer book unambiguously one-sided
    except Exception:
        return None


def _extract_gex_levels(agg: pd.DataFrame, df_sub: pd.DataFrame, spot: float,
                        q: float = 0.0, horizon_days: int = 1,
                        use_true_zero_gamma: bool = True) -> dict:
    """
    horizon_days: time horizon (trading days) for the expected-move band.
      • 0DTE chains  → 1 day (intraday session band)
      • HAG chains   → caller passes a Γ-OI-weighted average DTE, so the band
                       reflects the actual horizon of the aggregated book
                       rather than a hard-coded 1 day.
    q: continuous dividend yield of the underlying — used by the proper
       zero-gamma-spot root-finder.
    use_true_zero_gamma:
      • True  (default) — solves net GEX(S*) = 0 via brentq (canonical
        SqueezeMetrics formulation).
      • False — uses the original v1 cumulative-by-strike shortcut: the
        first strike at which running ΣGEX flips non-negative. Empirically
        this often locates a price-reaction zone that the true root misses,
        which is why the legacy mode keeps it as an option.
    """
    if agg is None or agg.empty:
        return {}
    call_wall = float(agg.loc[agg["gex"].idxmax(), "strike"])
    put_wall  = float(agg.loc[agg["gex"].idxmin(), "strike"])

    if use_true_zero_gamma:
        # Canonical SqueezeMetrics "Zero Gamma" via root-find on net GEX(S*) = 0.
        # Falls back to the cumulative-by-strike heuristic if no root in ±40%.
        gamma_flip_root = (_zero_gamma_spot(df_sub, spot, q=q)
                           if df_sub is not None else None)
        if gamma_flip_root is not None:
            gamma_flip = float(gamma_flip_root)
        else:
            pos_rows = agg[agg["cumgex"] >= 0]
            gamma_flip = float(pos_rows["strike"].iloc[0]) if not pos_rows.empty else spot
    else:
        # Legacy v1 shortcut: first strike where cumulative GEX ≥ 0.
        pos_rows = agg[agg["cumgex"] >= 0]
        gamma_flip = float(pos_rows["strike"].iloc[0]) if not pos_rows.empty else spot

    upper = lower = None
    if df_sub is not None and not df_sub.empty:
        atm_mask = abs(df_sub["strike"] - spot) / spot < 0.025
        atm_data = df_sub[atm_mask]
        if not atm_data.empty:
            atm_iv = atm_data["iv"].mean()
            if atm_iv > 0:
                iv_dec   = atm_iv / 100.0 if atm_iv > 1.0 else atm_iv
                # T-aware expected move: σ × √(horizon_days/252).
                # For 0DTE horizon_days=1 gives the intraday band; for HAG
                # the caller passes a Γ-OI-weighted DTE so the band matches
                # the actual aggregated-book horizon.
                exp_move = spot * iv_dec * math.sqrt(max(horizon_days, 1) / 252.0)
                upper    = round(spot + exp_move, 2)
                lower    = round(spot - exp_move, 2)
    return {
        "call_wall":   round(call_wall, 2),
        "put_wall":    round(put_wall, 2),
        "gamma_flip":  round(gamma_flip, 2),
        "upper_price": upper,
        "lower_price": lower,
    }


def _gamma_weighted_avg_dte(df_sub: pd.DataFrame) -> int:
    """
    OI-×-γ weighted average DTE across an aggregated chain. Used as the
    horizon for the HAG expected-move band so the upper/lower prices
    reflect the actual horizon of the dealer book rather than 1 day.
    """
    if df_sub is None or df_sub.empty:
        return 1
    today = _date.today()
    Ts, ws = [], []
    for _, r in df_sub.iterrows():
        try:
            d = _datetime.strptime(r["exp"], "%Y-%m-%d").date()
            dte = max((d - today).days, 0)
        except Exception:
            continue
        w = float(r.get("gamma", 0) or 0) * float(r.get("oi", 0) or 0)
        if w <= 0 or dte <= 0:
            continue
        Ts.append(dte); ws.append(w)
    if not Ts or sum(ws) <= 0:
        return 1
    avg = sum(t * w for t, w in zip(Ts, ws)) / sum(ws)
    return max(1, int(round(avg)))


def _secondary_walls_from_agg(agg: pd.DataFrame, primary_call: float,
                              primary_put: float) -> tuple:
    """Second-largest GEX strikes on call/put side (above & below primaries)."""
    if agg is None or agg.empty:
        return (None, None)
    pos = agg[agg["gex"] > 0].sort_values("gex", ascending=False)
    neg = agg[agg["gex"] < 0].sort_values("gex", ascending=True)
    sec_call = float(pos.iloc[1]["strike"]) if len(pos) > 1 else None
    sec_put  = float(neg.iloc[1]["strike"]) if len(neg) > 1 else None
    return (sec_call, sec_put)


@st.cache_data(ttl=120)
def compute_futures_gex(mode: str = "swing") -> dict:
    """
    GEX Radar for ES / NQ / GC.

    mode = "swing"    → 6-expiry HAG + same-day 0DTE block.
                        Horizon: Γ-OI-weighted avg DTE (typically 20-50d).
                        True zero-gamma-spot via brentq root-find.
                        Best for: multi-day to multi-week positions.

    mode = "daytrade" → Single DT block built from the *intraday* expiry only:
                        0DTE if available today, else nearest expiry with DTE≤5
                        (covers NDX MWF and weekly GLD). Tighter ±10% strike
                        range. Expected-move band uses 1-day horizon (full
                        session move). True zero-gamma-spot root-find.
                        Best for: same-day open & close.

    mode = "legacy"   → Reproduces the original commit (4c18e3f) math exactly:
                        6-expiry HAG, ±20% strikes, 1-day expected-move band
                        ALWAYS (not T-weighted), gamma flip computed as the
                        first strike where cumulative ΣGEX ≥ 0 (NOT the true
                        zero-gamma-spot). Kept because the cumulative-by-strike
                        heuristic empirically hits real price-reaction zones
                        for day traders even though it isn't the canonical
                        SqueezeMetrics formulation.

    All modes return mode-tagged level dicts so the UI can render any of them
    without knowing the math inside.
    """
    results = {}
    for fut_sym, (cboe_sym, is_index, _) in _FUTURES_CBOE.items():
        try:
            raw    = _fetch_cboe_gex_raw(cboe_sym, is_index)
            data   = raw.get("data", {})
            spot_r = float(data.get("current_price") or data.get("close") or 0)
            if not spot_r:
                results[fut_sym] = {"error": "No spot price returned by CBOE"}
                continue

            # ── Determine spot and scale factor ──────────────────────────────
            # For ES and NQ: SPX/NDX index price == ES/NQ futures price (direct mapping).
            # For GC: GLD ETF is not exactly GC/10 — compute live ratio from yfinance.
            if fut_sym == "GC":
                gc_price = _fetch_yf_spot(_YF_FUTURES["GC"])
                if gc_price and spot_r > 0:
                    scale = gc_price / spot_r   # e.g. 4557 / 418 = 10.90
                else:
                    scale = 10.0                # fallback if yfinance unavailable
                spot = round(gc_price or spot_r * scale, 2)
            else:
                yf_spot = _fetch_yf_spot(_YF_FUTURES[fut_sym])
                spot    = round(yf_spot or spot_r, 2)
                scale   = 1.0

            q_div = _DIV_YIELD.get(fut_sym, 0.0)

            opts = data.get("options", [])
            if not opts:
                results[fut_sym] = {"error": "No options returned by CBOE"}
                continue

            # Day-trade mode uses a tighter strike window (±10%) — extreme
            # OTM strikes contribute negligible γ at <5 DTE and only add
            # noise to the wall search.
            strike_range = 0.10 if mode == "daytrade" else 0.20
            df = _parse_options_gex(opts, spot_r, scale, q=q_div,
                                    strike_range=strike_range)
            if df.empty:
                results[fut_sym] = {"error": "No valid options after strike filter"}
                continue

            exps_all = sorted(df["exp"].unique())
            today_str = _date.today().isoformat()

            if mode == "daytrade":
                # ── DAY-TRADE BLOCK ──────────────────────────────────────────
                # Use 0DTE if today is an expiry; else nearest expiry within
                # 5 DTE. Covers SPX (daily M-F), NDX (MWF), GLD (weekly).
                # If even that fails (long weekend Sun), fall back to the
                # single nearest expiry no matter the DTE.
                df_dt = df[df["is_0dte"]].copy()
                used_expiries = [today_str] if not df_dt.empty else []

                if df_dt.empty:
                    nearest_dte = None
                    for exp in exps_all:
                        dte = (_datetime.strptime(exp, "%Y-%m-%d").date()
                               - _date.today()).days
                        if 0 <= dte <= 5:
                            nearest_dte = exp
                            break
                    if nearest_dte:
                        df_dt = df[df["exp"] == nearest_dte].copy()
                        used_expiries = [nearest_dte]
                    elif exps_all:
                        df_dt = df[df["exp"] == exps_all[0]].copy()
                        used_expiries = [exps_all[0]]

                if df_dt.empty:
                    results[fut_sym] = {"error": "No tradeable expiry within 5 DTE"}
                    continue

                agg_dt = _agg_gex_df(df_dt, spot)
                dt_lv  = _extract_gex_levels(agg_dt, df_dt, spot,
                                             q=q_div, horizon_days=1)
                sec_c, sec_p = _secondary_walls_from_agg(
                    agg_dt, dt_lv.get("call_wall"), dt_lv.get("put_wall"))
                dt_lv["secondary_call_wall"] = sec_c
                dt_lv["secondary_put_wall"]  = sec_p

                # Day-trade bias for the UI plan panel
                flip = dt_lv.get("gamma_flip")
                cwall = dt_lv.get("call_wall")
                pwall = dt_lv.get("put_wall")
                if flip is None:
                    bias = "n/a"
                elif spot > flip:
                    bias = "LONG bias (above zero-gamma — dealer pin / mean-revert)"
                else:
                    bias = "SHORT bias (below zero-gamma — dealer amplify / trend down)"
                # Wall-proximity override
                if cwall and abs(spot - cwall) / spot < 0.005:
                    bias = f"REJECT bias at call wall {cwall:.2f} — fade longs / scalp short"
                elif pwall and abs(spot - pwall) / spot < 0.005:
                    bias = f"BOUNCE bias at put wall {pwall:.2f} — fade shorts / scalp long"

                results[fut_sym] = {
                    "spot":       spot,
                    "spot_cboe":  round(spot_r * scale, 2),
                    "mode":       "daytrade",
                    "DT":         dt_lv,
                    "exps":       used_expiries,
                    "cboe":       cboe_sym,
                    "scale":      round(scale, 4),
                    "q_div":      q_div,
                    "bias":       bias,
                    "is_0dte":    used_expiries == [today_str],
                }

            elif mode == "legacy":
                # ── LEGACY v1 (original commit 4c18e3f exactly) ──────────────
                # • HAG = 6 nearest expiries, ±20% strike range
                # • Gamma flip = cumulative-by-strike first non-negative
                #   (NOT the true zero-gamma-spot root)
                # • Expected-move band = 1-day ALWAYS (spot × IV × √(1/252))
                # • NO secondary walls — only primary call/put wall
                df_hag   = df[df["exp"].isin(exps_all[:6])].copy()
                agg_hag  = _agg_gex_df(df_hag, spot)
                hag      = _extract_gex_levels(agg_hag, df_hag, spot,
                                               q=q_div, horizon_days=1,
                                               use_true_zero_gamma=False)

                df_0dte  = df[df["is_0dte"]].copy()
                if not df_0dte.empty:
                    agg_0dte = _agg_gex_df(df_0dte, spot)
                    dte      = _extract_gex_levels(agg_0dte, df_0dte, spot,
                                                   q=q_div, horizon_days=1,
                                                   use_true_zero_gamma=False)
                else:
                    dte = {}

                results[fut_sym] = {
                    "spot":       spot,
                    "spot_cboe":  round(spot_r * scale, 2),
                    "mode":       "legacy",
                    "HAG":        hag,
                    "0DTE":       dte,
                    "exps":       exps_all[:6],
                    "cboe":       cboe_sym,
                    "scale":      round(scale, 4),
                    "q_div":      q_div,
                }

            else:
                # ── SWING / POSITION MODE ────────────────────────────────────
                df_hag   = df[df["exp"].isin(exps_all[:6])].copy()
                agg_hag  = _agg_gex_df(df_hag, spot)
                hag_dte  = _gamma_weighted_avg_dte(df_hag)
                hag      = _extract_gex_levels(agg_hag, df_hag, spot,
                                               q=q_div, horizon_days=hag_dte)
                sec_c, sec_p = _secondary_walls_from_agg(
                    agg_hag, hag.get("call_wall"), hag.get("put_wall"))
                hag["secondary_call_wall"] = sec_c
                hag["secondary_put_wall"]  = sec_p

                df_0dte  = df[df["is_0dte"]].copy()
                if not df_0dte.empty:
                    agg_0dte = _agg_gex_df(df_0dte, spot)
                    dte      = _extract_gex_levels(agg_0dte, df_0dte, spot,
                                                   q=q_div, horizon_days=1)
                else:
                    dte = {}

                results[fut_sym] = {
                    "spot":       spot,
                    "spot_cboe":  round(spot_r * scale, 2),
                    "mode":       "swing",
                    "HAG":        hag,
                    "0DTE":       dte,
                    "exps":       exps_all[:6],
                    "cboe":       cboe_sym,
                    "scale":      round(scale, 4),
                    "q_div":      q_div,
                    "hag_horizon_days": hag_dte,
                }
        except Exception as exc:
            results[fut_sym] = {"error": str(exc)}
    return results


def build_gex_pipe_string(results: dict) -> str:
    """
    Pipe-delimited SYMBOL:PRICE:LABEL string for algo / TradingView input.

    Schema depends on mode (auto-detected from results):
      • daytrade → DT block only (walls + secondary + flip + 1σ band)
      • swing    → HAG + 0DTE blocks (current swing schema)
      • legacy   → original v1 schema: HAG-only, no secondary walls, exactly
                   matches commit 4c18e3f output for backwards compatibility
                   with TradingView templates / algos that consume the v1 keys
    """
    _ORDER_SWING = [
        ("HAG",  "call_wall",   "HAG Call Wall"),
        ("0DTE", "call_wall",   "0DTE Call Wall"),
        ("HAG",  "gamma_flip",  "HAG Gamma Flip"),
        ("0DTE", "gamma_flip",  "0DTE Gamma Flip"),
        ("HAG",  "upper_price", "HAG Upper Price"),
        ("HAG",  "put_wall",    "HAG Put Wall"),
        ("HAG",  "lower_price", "HAG Lower Price"),
        ("0DTE", "upper_price", "0DTE Upper Price"),
        ("0DTE", "put_wall",    "0DTE Put Wall"),
        ("0DTE", "lower_price", "0DTE Lower Price"),
    ]
    _ORDER_DT = [
        ("DT", "call_wall",           "DT Call Wall"),
        ("DT", "secondary_call_wall", "DT Call Wall 2"),
        ("DT", "gamma_flip",          "DT Gamma Flip"),
        ("DT", "upper_price",         "DT Upper 1σ"),
        ("DT", "put_wall",            "DT Put Wall"),
        ("DT", "secondary_put_wall",  "DT Put Wall 2"),
        ("DT", "lower_price",         "DT Lower 1σ"),
    ]
    # v1 LEGACY schema — must exactly match the original commit 4c18e3f
    # so existing TradingView templates / NQ algos keep working.
    _ORDER_LEGACY = [
        ("HAG",  "call_wall",   "HAG Call Wall"),
        ("HAG",  "gamma_flip",  "HAG Gamma Flip"),
        ("HAG",  "upper_price", "HAG Upper Price"),
        ("HAG",  "put_wall",    "HAG Put Wall"),
        ("HAG",  "lower_price", "HAG Lower Price"),
    ]
    parts = []
    for sym in ["ES", "NQ", "GC"]:
        d = results.get(sym, {})
        if "error" in d:
            continue
        m = d.get("mode")
        if m == "daytrade":
            order = _ORDER_DT
        elif m == "legacy":
            order = _ORDER_LEGACY
        else:
            order = _ORDER_SWING
        for blk, key, label in order:
            val = d.get(blk, {}).get(key)
            if val is not None:
                parts.append(f"{sym}:{val:.0f}:{label}")
    return "|".join(parts)

st.set_page_config(
    page_title="Quantitative Options Engine",
    layout="wide",
)

import theme as _theme
_theme.apply()

# ─── CLI capture helper ────────────────────────────────────────────────────────

def run_cli_function(func, prompt_map, *args, **kwargs):
    """
    Runs a function that uses input()/print(), capturing stdout.
    prompt_map: {substring_of_prompt_lowercase: answer_string}
    Handles rate-limit errors gracefully — surfaces them in the output
    instead of crashing the app.
    """
    old_input  = builtins.input
    old_stdout = sys.stdout

    def mocked_input(prompt=""):
        prompt_lower = prompt.lower()
        print(prompt, end="")
        for key, val in prompt_map.items():
            if key and key in prompt_lower:
                print(str(val))
                return str(val)
        print("")
        return ""

    builtins.input = mocked_input
    captured = io.StringIO()
    sys.stdout = captured

    try:
        result = func(*args, **kwargs)
        output = captured.getvalue()
    except Exception as e:
        output = captured.getvalue()
        err    = str(e)
        # Surface rate-limit errors with friendly guidance
        if "429" in err or "rate limit" in err.lower() or "too many requests" in err.lower():
            output += (
                "\n\n  RATE LIMIT HIT (Yahoo Finance / yfinance)\n"
                "─────────────────────────────────────────────\n"
                "Yahoo Finance is temporarily blocking requests from this server.\n"
                "Fixes:\n"
                "  1. Wait 60–120 seconds and click Run again.\n"
                "  2. Use a smaller universe (fewer stocks) for Scanner modes.\n"
                "  3. If on Streamlit Cloud, multiple users may share the same IP.\n"
                "     Consider running locally for heavy scans.\n"
            )
        elif "401" in err or "unauthorized" in err.lower():
            output += (
                "\n\n  DATA ACCESS ERROR (Yahoo Finance HTTP 401)\n"
                "──────────────────────────────────────────────\n"
                "Yahoo Finance blocked this server's IP (cloud IPs are sometimes banned).\n"
                "The engine has automatically switched to the standard yfinance fallback.\n"
                "Fixes:\n"
                "  1. Click Run again — the fallback mode is now active and should work.\n"
                "  2. If it persists, wait 30 seconds and retry.\n"
                "  3. For heavy scans, run the tool locally to avoid cloud IP blocks.\n"
            )
        else:
            output += f"\n\nERROR: {err}"
        result = None
    finally:
        builtins.input = old_input
        sys.stdout     = old_stdout

    return result, output


# ─── Sidebar ───────────────────────────────────────────────────────────────────

st.sidebar.header(" Settings")
st.sidebar.markdown("**AI Synthesis** — connect an LLM API to get natural language analysis of results.")
api_key      = st.sidebar.text_input("API Key", type="password", help="Optional. Used only for AI Synthesis button.")
api_base_url = st.sidebar.text_input("API Base URL", value="https://api.megallm.io/v1")
selected_model = st.sidebar.text_input("Model", value="gpt-4o")

st.sidebar.divider()
st.sidebar.markdown(
    "**Rate limit tip:** Yahoo Finance limits ~100 req/min per IP. "
    "If you see a rate-limit error, wait 60–120 s and retry."
)

# ─── Session state ─────────────────────────────────────────────────────────────

for key in ("cli_output", "df_result"):
    if key not in st.session_state:
        st.session_state[key] = None

# ─── Header ───────────────────────────────────────────────────────────────────

st.title(" Quantitative Options Engine")
st.markdown(
    "Select a mode, configure parameters, and click **Run**. "
    "Results appear below. Use **AI Synthesis** to get a plain-English interpretation."
)

mode = st.selectbox("Select Mode", [
    "1. Full Analysis",
    "2. Trade Finder",
    "3. Backtest Model",
    "4. Market Scanner",
    "5. Scanner Backtest",
    "6. GEX Radar (ES/NQ/GC)",
    "7. Prop Firm Risk Manager",
])

st.divider()

# ─── Mode UIs ─────────────────────────────────────────────────────────────────

if mode == "1. Full Analysis":
    st.subheader("Mode 1: Full Analysis")
    st.markdown(
        "Fetches the full option chain for all expiries, computes Greeks, "
        "GARCH vol forecast, SABR smile, pin risk, and income screeners (CSP / CC / Iron Condor)."
    )
    col1, col2 = st.columns(2)
    with col1:
        symbol     = st.text_input("Stock Symbol (e.g. AAPL)", "AAPL").strip().upper()
        run_strat  = st.checkbox("Run optionlab strategy analysis?", value=False)
    with col2:
        run_tf     = st.checkbox("Auto-run Trade Finder after?", value=False)

    if st.button(" Run Full Analysis", type="primary"):
        if symbol:
            with st.spinner(f"Running Full Analysis for {symbol}… (may take 30–60 s)"):
                pm = {
                    "enter stock symbol":        symbol,
                    "run strategy analysis":     "y" if run_strat else "n",
                    "run trade finder":          "y" if run_tf else "n",
                }
                res, out = run_cli_function(opt.main, pm)
                st.session_state.cli_output = out
                st.session_state.df_result  = None
        else:
            st.warning("Please enter a stock symbol.")

elif mode == "2. Trade Finder":
    st.subheader("Mode 2: Trade Finder")
    st.markdown(
        "Ranks every call/put in your DTE window by an 8-signal institutional score "
        "including **IV Rank**, **real-world EV** (P-measure), **RSI alignment**, and **Kelly sizing**."
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        symbol       = st.text_input("Stock Symbol (e.g. TSLA)", "TSLA").strip().upper()
        action       = st.selectbox("Buy or Sell?", ["b", "s"],
                                    format_func=lambda x: "Buy" if x == "b" else "Sell")
        opt_type     = st.selectbox("Direction (auto = GEX decides)", ["auto", "c", "p"],
                                    format_func=lambda x: {"auto":"Auto (GEX)","c":"Call","p":"Put"}[x])
    with col2:
        dte_min      = st.number_input("Min DTE", value=20, min_value=1)
        dte_max      = st.number_input("Max DTE", value=60, min_value=2)
    with col3:
        budget       = st.number_input("Max premium / Min credit ($)", value=5.00, min_value=0.01, step=0.50)
        target_delta = st.text_input("Target Delta (optional, e.g. 0.30)", value="")

    if st.button(" Run Trade Finder", type="primary"):
        if symbol:
            with st.spinner(f"Finding best {opt_type.upper() if opt_type != 'auto' else 'Call/Put'} trades for {symbol}…"):
                # GEX override answer: "" means accept GEX suggestion, "c"/"p" overrides
                gex_ans = "" if opt_type == "auto" else opt_type
                pm = {
                    "symbol":               symbol,
                    "buy or sell":          action,
                    # GEX prompt: "GEX suggests CALL — press Enter to accept or type [c/p]"
                    "gex suggests":         gex_ans,
                    # Fallback if GEX unavailable: "Call or Put?  [c/p]"
                    "call or put":          opt_type if opt_type != "auto" else "c",
                    "min days to expiry":   str(int(dte_min)),
                    "max days to expiry":   str(int(dte_max)),
                    "max premium":          str(budget),
                    "min credit":           str(budget),
                    "target delta":         target_delta,
                }
                res, out = run_cli_function(opt.find_trade, pm)
                st.session_state.cli_output = out
                st.session_state.df_result  = None
        else:
            st.warning("Please enter a symbol.")

elif mode == "3. Backtest Model":
    st.subheader("Mode 3: Backtest Model (single stock)")
    st.markdown(
        "Replays the option-buying model on one stock historically using BS-priced synthetic options."
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        symbol    = st.text_input("Stock Symbol (e.g. AAPL)", "AAPL").strip().upper()
        action    = st.selectbox("Buy or Sell?", ["b", "s"],
                                 format_func=lambda x: "Buy" if x == "b" else "Sell")
        direction = st.selectbox("Direction", ["a", "c", "p"],
                                 format_func=lambda x: {"a":"Auto-momentum","c":"Call","p":"Put"}[x])
        budget    = st.number_input("Max Premium / Min Credit ($)", value=5.00, min_value=0.01, step=0.50)
    with col2:
        target_dte = st.number_input("Target DTE at entry", value=30, min_value=5)
        lookback   = st.number_input("Lookback Days", value=252, min_value=60)
        exit_dte   = st.number_input("Exit at X DTE remaining (0=hold to expiry)", value=0, min_value=0)
    with col3:
        tp = st.number_input("Take profit % (0=none)", value=100, min_value=0)
        sl = st.number_input("Stop loss %  (0=none)", value=50,  min_value=0)
        be = st.number_input("Break-even trigger % (0=none)", value=0, min_value=0)

    if st.button(" Run Backtest", type="primary"):
        if symbol:
            with st.spinner(f"Running historical backtest for {symbol}…"):
                pm = {
                    "symbol":       symbol,
                    "buy or sell":  action,
                    "direction":    direction,
                    "target dte":   str(int(target_dte)),
                    "lookback":     str(int(lookback)),
                    "premium":      str(budget),
                    "credit":       str(budget),
                    "take profit":  str(int(tp)),
                    "break-even":   str(int(be)),
                    "stop loss":    str(int(sl)),
                    "exit at":      str(int(exit_dte)),
                }
                res, out = run_cli_function(opt.backtest_model, pm)
                st.session_state.cli_output = out
                st.session_state.df_result  = res if isinstance(res, pd.DataFrame) else None
        else:
            st.warning("Please enter a symbol.")

elif mode == "4. Market Scanner":
    st.subheader("Mode 4: Market Scanner")
    st.markdown(
        "Scans up to 600 stocks (S&P 500 + Nasdaq-100 + CBOE most-active) "
        "and ranks them by an IV-HV / GEX / GARCH composite score."
    )
    st.warning(
        " Large scans hit Yahoo Finance's rate limit quickly on cloud deployments. "
        "Keep **Max stocks** ≤ 50 on Streamlit Cloud, or use a custom watchlist."
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        budget     = st.number_input("Max premium per contract ($)", value=5.00, min_value=0.01, step=0.50)
    with col2:
        max_stocks = st.number_input("Max stocks to scan", value=30, min_value=5, max_value=300)
    with col3:
        watchlist_raw = st.text_input("Custom watchlist (comma-separated, or leave blank for auto)", value="")

    watchlist = [t.strip().upper() for t in watchlist_raw.split(',') if t.strip()] if watchlist_raw else None

    if st.button(" Run Scanner", type="primary"):
        with st.spinner(f"Scanning {max_stocks} stocks… this takes 2–5 minutes…"):
            res, out = run_cli_function(
                opt.market_scanner, {},
                budget=float(budget), max_stocks=int(max_stocks),
                watchlist=watchlist,
            )
            st.session_state.cli_output = out
            st.session_state.df_result  = res if isinstance(res, pd.DataFrame) else None

elif mode == "5. Scanner Backtest":
    st.subheader("Mode 5: Scanner Backtest (v2 — improved model)")
    st.markdown(
        "Replays the market scanner historically with improved position sizing, "
        "vol-cheap gate, IV percentile filter, and SPY regime filter."
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        watchlist_raw = st.text_input("Watchlist (e.g. AAPL,TSLA,SOFI — blank for default 70)", value="")
        lookback      = st.number_input("Lookback Days", value=252, min_value=60)
        holding       = st.number_input("Max Holding Days per trade", value=14, min_value=3)
    with col2:
        scan_freq = st.number_input("Scan every N trading days", value=14, min_value=1)
        top_n     = st.number_input("Top N trades per scan", value=3, min_value=1, max_value=10)
        acct      = st.number_input("Starting Account ($)", value=190.0, min_value=10.0)
    with col3:
        budget_bt = st.number_input("Max cost per contract ($)", value=5.00, min_value=0.01, step=0.50)
        tp_bt     = st.number_input("Take profit %", value=50, min_value=5)
        sl_bt     = st.number_input("Stop loss %",   value=60, min_value=5)

    watchlist = [t.strip().upper() for t in watchlist_raw.split(',') if t.strip()] if watchlist_raw else None

    if st.button(" Run Scanner Backtest", type="primary"):
        with st.spinner("Backtesting scanner signals historically… (~2–5 min for default universe)"):
            res, out = run_cli_function(
                opt.backtest_scanner, {},
                watchlist=watchlist,
                lookback_days=int(lookback),
                holding_days=int(holding),
                scan_freq=int(scan_freq),
                top_n=int(top_n),
                account=float(acct),
                budget=float(budget_bt),
                take_profit=tp_bt / 100,
                stop_loss=sl_bt / 100,
            )
            st.session_state.cli_output = out
            st.session_state.df_result  = res if isinstance(res, pd.DataFrame) else None

elif mode == "6. GEX Radar (ES/NQ/GC)":
    st.subheader("Mode 6: GEX Radar — Futures Key Levels")
    st.markdown(
        "Computes **Gamma Exposure (GEX)** levels for **ES**, **NQ**, and **GC** "
        "from CBOE options data (SPX / NDX / GLD). "
        "Outputs a pipe-delimited string in `SYMBOL:PRICE:LABEL` format for direct "
        "use in your NQ trading algo or TradingView price alerts.\n\n"
        "_Data: CBOE free CDN — 15-min delayed quotes, OI refreshes after market close._"
    )

    sel_col, btn_col = st.columns([5, 1])
    with sel_col:
        radar_mode_label = st.radio(
            "Trading mode",
            [
                "Day Trade (close intraday)",
                "Swing / Position (HAG multi-day)",
                "Legacy v1 (HAG · cumulative flip · 1d band)",
            ],
            horizontal=True,
            help=(
                "**Day Trade** — 0DTE or nearest expiry ≤5 DTE, 1-day band, "
                "±10% strikes, true zero-gamma-spot, bias panel.\n\n"
                "**Swing** — 6-expiry HAG, Γ-OI-weighted multi-day horizon, "
                "true zero-gamma-spot, T-aware expected-move.\n\n"
                "**Legacy v1** — exact reproduction of the original commit "
                "(4c18e3f) math: 6-expiry HAG, ±20% strikes, 1-day band ALWAYS, "
                "gamma flip = first strike where cumulative ΣGEX ≥ 0 (not the "
                "true zero-gamma-spot). Empirically this often locates a real "
                "price-reaction zone for day traders even though it isn't the "
                "canonical SqueezeMetrics formulation."
            ),
        )
        if radar_mode_label.startswith("Day"):
            radar_mode = "daytrade"
        elif radar_mode_label.startswith("Legacy"):
            radar_mode = "legacy"
        else:
            radar_mode = "swing"
    with btn_col:
        st.write("")  # vertical spacer to align button with the radio
        if st.button("Refresh", type="primary"):
            compute_futures_gex.clear()
            _fetch_yf_spot.clear()

    # Charm-acceleration window (Skavinski G7, SqueezeMetrics):
    # 1:30-3:00 PM ET — dealers aggressively rebalance overnight charm
    # exposure as 0DTE delta decays toward expiry. Trend amplification.
    import pytz
    _et_now = _datetime.now(pytz.timezone("America/New_York"))
    _et_min = _et_now.hour * 60 + _et_now.minute
    in_charm_window = (_et_now.weekday() < 5) and (810 <= _et_min < 900)  # 13:30 .. 15:00
    if radar_mode == "daytrade" and in_charm_window:
        st.warning(
            " **Charm-acceleration window active** (1:30-3:00 PM ET). "
            "Dealers are rebalancing overnight charm exposure now — "
            "trend moves get *amplified* and pin attraction *weakens* "
            "until the close. Tighten stops; expect trend continuation."
        )

    with st.spinner("Fetching CBOE options: SPX (ES) / NDX (NQ) / GLD (GC)…"):
        gex_results = compute_futures_gex(mode=radar_mode)

    # ── Live spot price verification ──────────────────────────────────────────
    st.divider()
    st.markdown("#### Live Spot Prices (yfinance futures verification)")
    vc = st.columns(3)
    for i, sym in enumerate(["ES", "NQ", "GC"]):
        with vc[i]:
            d = gex_results.get(sym, {})
            if "spot" in d:
                st.metric(
                    label=f"{sym} futures spot",
                    value=f"{d['spot']:,.2f}",
                    help=f"yfinance {_YF_FUTURES[sym]} — used as GEX anchor. "
                         f"CBOE {d.get('cboe','')} equivalent: {d.get('spot_cboe',0):,.2f}"
                )
            else:
                st.metric(label=f"{sym}", value="N/A", delta=d.get("error",""))

    # ── Pipe string for algo ──────────────────────────────────────────────────
    pipe_str = build_gex_pipe_string(gex_results)
    st.divider()
    st.markdown("#### Algo String — copy into TradingView / strategy input")
    if pipe_str:
        st.code(pipe_str, language=None)
        st.caption(
            "Format: `SYMBOL:PRICE:LABEL` — pipe-separated. "
            "Matches gexradar.io header convention."
        )
    else:
        st.warning("No live data available — CBOE may be unreachable or market is closed.")

    # ── Per-instrument tables ─────────────────────────────────────────────────
    st.divider()
    st.markdown("#### Key Levels by Instrument")

    _LEVEL_ROWS_SWING = [
        ("HAG",  "call_wall",   "HAG Call Wall",    "Largest call gamma strike — dealer resistance ceiling"),
        ("0DTE", "call_wall",   "0DTE Call Wall",   "Today's largest call gamma — intraday ceiling"),
        ("HAG",  "gamma_flip",  "HAG Gamma Flip",   "TRUE zero-gamma spot S* — regime pivot (above=mean-revert, below=trend)"),
        ("0DTE", "gamma_flip",  "0DTE Gamma Flip",  "Today's true zero-gamma spot"),
        ("HAG",  "upper_price", "HAG Upper Price",  "1σ upside expected move over Γ-OI-weighted avg DTE"),
        ("HAG",  "put_wall",    "HAG Put Wall",     "Largest put gamma strike — dealer support floor"),
        ("HAG",  "lower_price", "HAG Lower Price",  "1σ downside expected move over Γ-OI-weighted avg DTE"),
        ("0DTE", "upper_price", "0DTE Upper Price", "Intraday session ceiling from 0DTE ATM IV (1d)"),
        ("0DTE", "put_wall",    "0DTE Put Wall",    "Today's largest put gamma — intraday support"),
        ("0DTE", "lower_price", "0DTE Lower Price", "Intraday session floor from 0DTE ATM IV (1d)"),
    ]
    _LEVEL_ROWS_DT = [
        ("DT", "upper_price",         "Upper 1σ (TP for longs)",  "1-day 1σ ceiling — typical session high cap"),
        ("DT", "secondary_call_wall", "Call Wall 2 (extension)",  "Secondary resistance if Wall 1 breaks"),
        ("DT", "call_wall",           "Call Wall (resistance)",   "Primary ceiling — dealers SELL hedges here"),
        ("DT", "gamma_flip",          "Zero Gamma (regime pivot)","Above = pin/fade. Below = trend/follow."),
        ("DT", "put_wall",            "Put Wall (support)",       "Primary floor — dealers BUY hedges here"),
        ("DT", "secondary_put_wall",  "Put Wall 2 (extension)",   "Secondary support if Wall 1 breaks"),
        ("DT", "lower_price",         "Lower 1σ (TP for shorts)", "1-day 1σ floor — typical session low cap"),
    ]

    cols = st.columns(3)
    for idx, sym in enumerate(["ES", "NQ", "GC"]):
        with cols[idx]:
            data = gex_results.get(sym, {})
            proxy = {"ES": "SPX", "NQ": "NDX", "GC": "GLD × 10"}[sym]

            if "error" in data:
                st.error(f"**{sym}** ({proxy}): {data['error']}")
                continue

            spot = data["spot"]
            exps = data.get("exps", [])

            spot_str = f"{spot:,.0f}" if spot >= 1000 else f"{spot:,.2f}"
            st.markdown(f"**{sym}** via {proxy} — spot `{spot_str}`")
            _qd  = data.get("q_div", 0.0)
            if data.get("mode") == "daytrade":
                _exp_s = exps[0] if exps else "n/a"
                _badge = "0DTE" if data.get("is_0dte") else "nearest"
                st.caption(f"Expiry ({_badge}): {_exp_s}  ·  q={_qd*100:.1f}%")
            elif data.get("mode") == "legacy":
                st.caption(
                    f"v1 schema  ·  6 expiries: {', '.join(exps) if exps else 'n/a'}  "
                    f"·  1d band  ·  cumulative flip"
                )
            else:
                _hag_h = data.get("hag_horizon_days")
                _meta  = f"Expiries: {', '.join(exps) if exps else 'n/a'}"
                if _hag_h:
                    _meta += f"  ·  HAG horizon: ~{_hag_h}d  ·  q={_qd*100:.1f}%"
                st.caption(_meta)

            # Day-trade plan summary panel
            if data.get("mode") == "daytrade":
                bias = data.get("bias", "n/a")
                bias_color = ("" if "LONG" in bias
                              else "" if "SHORT" in bias
                              else "")
                st.markdown(f"{bias_color} **Bias:** {bias}")

            # Level table — choose rows based on mode
            if data.get("mode") == "daytrade":
                dt = data.get("DT", {})
                level_src = lambda blk: dt
                row_defs = _LEVEL_ROWS_DT
            elif data.get("mode") == "legacy":
                # v1 schema — only the original 5 HAG levels, no secondary walls,
                # no T-aware labels, no 0DTE block. Identical to commit 4c18e3f.
                hag = data.get("HAG", {})
                level_src = lambda blk: hag
                row_defs = [
                    ("HAG", "call_wall",   "HAG Call Wall",
                     "Largest call gamma strike (v1: aggregated across 6 expiries)"),
                    ("HAG", "gamma_flip",  "HAG Gamma Flip",
                     "v1 cumulative-by-strike flip — first strike where ΣGEX ≥ 0"),
                    ("HAG", "upper_price", "HAG Upper Price",
                     "v1: spot × IV × √(1/252) — 1-day expected upper move"),
                    ("HAG", "put_wall",    "HAG Put Wall",
                     "Largest put gamma strike (v1: aggregated across 6 expiries)"),
                    ("HAG", "lower_price", "HAG Lower Price",
                     "v1: spot − 1-day expected move"),
                ]
            else:
                hag = data.get("HAG", {})
                dte = data.get("0DTE", {})
                level_src = lambda blk: hag if blk == "HAG" else dte
                row_defs = _LEVEL_ROWS_SWING

            rows = []
            for blk, field, label, _desc in row_defs:
                val = level_src(blk).get(field)
                if val is None:
                    continue
                diff = val - spot
                rows.append({
                    "Level":   label,
                    "Price":   f"{val:,.2f}",
                    "vs Spot": f"+{diff:,.0f}" if diff >= 0 else f"{diff:,.0f}",
                })

            if rows:
                st.dataframe(
                    pd.DataFrame(rows),
                    width='stretch',
                    hide_index=True,
                )
            else:
                st.info("No levels computed — market may be closed or no usable expiry available.")

    # ── Regime interpretation ─────────────────────────────────────────────────
    st.divider()
    st.markdown("#### Regime Summary")
    reg_cols = st.columns(3)
    for idx, sym in enumerate(["ES", "NQ", "GC"]):
        with reg_cols[idx]:
            data = gex_results.get(sym, {})
            if "error" in data or "spot" not in data:
                continue
            spot = data["spot"]
            # In daytrade mode the levels live in the DT block; in swing
            # mode they live in HAG. Same regime logic applies to either.
            block = data.get("DT") if data.get("mode") == "daytrade" else data.get("HAG", {})
            flip       = block.get("gamma_flip") if block else None
            call_wall  = block.get("call_wall")  if block else None
            put_wall   = block.get("put_wall")   if block else None
            if flip is None:
                continue
            above_flip = spot >= flip
            regime     = "POSITIVE GEX — mean-reversion, fade extremes" if above_flip \
                         else "NEGATIVE GEX — trending, follow momentum"
            st.markdown(f"**{sym}**")
            st.markdown(f"`{regime}`")
            if call_wall:
                st.markdown(f"Call Wall: `{call_wall:,.0f}` | Put Wall: `{put_wall:,.0f}`")
            st.markdown(f"Gamma Flip: `{flip:,.0f}` | Spot: `{spot:,.0f}`")
            st.markdown("---")

    st.caption(
        "Research: Bollen & Whaley (2004) J.Finance — dealer delta-hedging at GEX walls "
        "creates mechanical price resistance/support. Amin et al. (2022) SSRN-4131538 — "
        "0DTE gamma pinning is systematic. Muravyev (2016) J.Finance — option flows "
        "predict underlying returns via hedge rebalancing."
    )


# ─── Mode 7: Prop Firm Risk Manager ───────────────────────────────────────────
#
# Built for funded-futures accounts (Apex, Topstep, MyFundedFutures, Earn2Trade,
# Take Profit Trader, FundedNext-Futures, TX3, etc.). Two phases:
#
#   EVAL   — hit a profit target without violating daily DD or trailing DD.
#            Per-trade risk is the lever. Most evals are blown by oversizing.
#   FUNDED — preserve eligibility, take payouts. Payouts have minimum-day
#            requirements (e.g. Apex: 5 winning days ≥ $200, 8 trading days
#            total since last payout, leave $1000 trailing DD buffer above
#            the trigger to remain in good standing).
#
# Math we apply:
#   • Fixed-fractional position sizing (Vince 1990 / Tharp 2008):
#         n_contracts = floor( risk_per_trade$ / (stop_ticks × tick_$) )
#   • Trailing drawdown is a hard floor that ratchets up with account peak
#     until balance crosses a "lock" level (Apex: peak+$100 once funded =
#     trailing stops moving; Topstep: trailing stops at initial balance).
#   • Daily DD is reset at session boundary (5 PM ET typically).
#   • Payout eligibility = active days × winning days × balance > trigger+buffer.

_PROP_FIRMS = {
    # firm: { account_size: (profit_target, trail_dd, daily_dd or None,
    #                        max_contracts, payout_min_winning_days,
    #                        payout_min_total_days, payout_buffer,
    #                        consistency_pct,         # best-day ≤ % of total
    #                        trail_lock_at_balance,   # balance trail locks at
    #                        trail_lock_floor) }      # static floor once locked
    #
    # Sources: Apex 4.0 (Mar 2026), Topstep Standard (post-Jan-2026),
    #          MyFundedFutures Trader Manual, Lucid Trading LucidFlex/Pro/Direct
    #          public rule pages (Nov 2026 snapshot). Always verify with the
    #          firm's current rulebook before sizing real trades.
    "Apex": {
        # Apex 4.0: trailing DD locks at peak = initial_balance + trail_dd + $100;
        # once locked, floor = initial_balance. 50% consistency rule (was 30%
        # pre-4.0). 5 winning days for payout. NO daily DD on EA accounts.
        25_000:  (1_500, 1_500,  None,  4, 5, 8, 100, 0.50, None, None),
        50_000:  (3_000, 2_500,  None, 10, 5, 8, 100, 0.50, None, None),
        75_000:  (4_250, 2_750,  None, 12, 5, 8, 100, 0.50, None, None),
        100_000: (6_000, 3_000,  None, 14, 5, 8, 100, 0.50, None, None),
        150_000: (9_000, 5_000,  None, 17, 5, 8, 100, 0.50, None, None),
    },
    "Topstep": {
        # Topstep: trailing DD locks PERMANENTLY at initial starting balance
        # when peak reaches size + trail_dd. 50% consistency rule (eval only).
        # 5 winning days + 30 total since last payout. Daily DD is soft breach
        # (auto-liquidate, no rule violation).
        50_000:  (3_000, 2_000,  1_000,  5, 5, 30, 0, 0.50, 52_000,  50_000),
        100_000: (6_000, 3_000,  2_000, 10, 5, 30, 0, 0.50, 103_000, 100_000),
        150_000: (9_000, 4_500,  3_000, 15, 5, 30, 0, 0.50, 154_500, 150_000),
    },
    "MyFundedFutures": {
        # MFFU: EOD trailing DD, locks at peak = initial + trail_dd, floor =
        # initial. Daily DD = 25% of trail DD. 40% consistency rule. 5 winning
        # days + 10 total days for payout.
        50_000:  (3_000, 2_000,  1_250,  5, 5, 10, 100, 0.40, 52_000,  50_000),
        100_000: (6_000, 3_000,  2_500, 10, 5, 10, 100, 0.40, 103_000, 100_000),
        150_000: (9_000, 4_500,  3_750, 15, 5, 10, 100, 0.40, 154_500, 150_000),
    },
    "Lucid (Flex)": {
        # LucidFlex: NO daily loss limit. 50% consistency in eval, NONE funded.
        # EOD trailing DD that locks at peak = initial + trail_dd.
        # 5 winning days for payout. Profit split 90/10 since Mar 2026.
        25_000:  (1_250, 1_000,  None,  2, 5, 5, 0, 0.50, 26_000,  25_000),
        50_000:  (3_000, 2_000,  None,  4, 5, 5, 0, 0.50, 52_000,  50_000),
        100_000: (6_000, 3_000,  None,  6, 5, 5, 0, 0.50, 103_000, 100_000),
        150_000: (9_000, 4_500,  None, 10, 5, 5, 0, 0.50, 154_500, 150_000),
    },
    "Lucid (Pro)": {
        # LucidPro: DLL activates at $50k+. 40% consistency rule once funded.
        # Otherwise same trail-DD mechanics as Flex.
        25_000:  (1_250, 1_000,  None,  2, 5, 5, 0, 0.40, 26_000,  25_000),
        50_000:  (3_000, 2_000,  1_200, 4, 5, 5, 0, 0.40, 52_000,  50_000),
        100_000: (6_000, 3_000,  1_800, 6, 5, 5, 0, 0.40, 103_000, 100_000),
        150_000: (9_000, 4_500,  2_700,10, 5, 5, 0, 0.40, 154_500, 150_000),
    },
    "Lucid (Direct)": {
        # LucidDirect: instant-funded (no eval). 20% consistency on every
        # payout. Tighter DLL. Trail-lock at peak = initial + trail_dd.
        25_000:  (   0, 1_000,  None,  2, 0, 0, 0, 0.20, 26_000,  25_000),
        50_000:  (   0, 2_000,  1_200, 4, 0, 0, 0, 0.20, 52_000,  50_000),
        100_000: (   0, 3_500,  2_100, 6, 0, 0, 0, 0.20, 103_500, 100_000),
        150_000: (   0, 5_000,  3_000,10, 0, 0, 0, 0.20, 155_000, 150_000),
    },
}

# Tick value per instrument (CME official). NB: micro versions are 1/10.
_TICK_INFO = {
    "ES":  {"tick_size": 0.25, "tick_value": 12.50,  "label": "S&P 500 E-mini"},
    "MES": {"tick_size": 0.25, "tick_value":  1.25,  "label": "S&P 500 Micro"},
    "NQ":  {"tick_size": 0.25, "tick_value":  5.00,  "label": "Nasdaq-100 E-mini"},
    "MNQ": {"tick_size": 0.25, "tick_value":  0.50,  "label": "Nasdaq-100 Micro"},
    "YM":  {"tick_size": 1.00, "tick_value":  5.00,  "label": "Dow E-mini"},
    "MYM": {"tick_size": 1.00, "tick_value":  0.50,  "label": "Dow Micro"},
    "GC":  {"tick_size": 0.10, "tick_value": 10.00,  "label": "Gold"},
    "MGC": {"tick_size": 0.10, "tick_value":  1.00,  "label": "Gold Micro"},
    "CL":  {"tick_size": 0.01, "tick_value": 10.00,  "label": "WTI Crude"},
    "MCL": {"tick_size": 0.01, "tick_value":  1.00,  "label": "Crude Micro"},
    "RTY": {"tick_size": 0.10, "tick_value":  5.00,  "label": "Russell 2000"},
    "M2K": {"tick_size": 0.10, "tick_value":  0.50,  "label": "Russell Micro"},
}


def _round_contracts(risk_dollar, stop_ticks, tick_value, max_contracts):
    if stop_ticks <= 0 or tick_value <= 0:
        return 0
    raw = int(risk_dollar // (stop_ticks * tick_value))
    return max(0, min(raw, max_contracts))


# ─── Risk-management math (Kelly, expectancy, gambler's ruin) ─────────────────

def _expectancy_per_trade(p_win: float, R: float, per_trade_risk_dollar: float
                          ) -> dict:
    """
    Per-trade expectancy (μ) and standard deviation (σ) in dollars.

    For a binary trade that wins R × L with prob p and loses L with prob q,
    closed-form moments:
        μ_per_trade = L × (p·R − q)
        σ_per_trade = L × √(pq) × (R + 1)        ← clean closed form

    Returns dict with mu, sigma, expectancy_R (in R-multiples), edge_pct.
    """
    if not (0 < p_win < 1) or R <= 0 or per_trade_risk_dollar <= 0:
        return {"mu": math.nan, "sigma": math.nan, "expectancy_R": math.nan,
                "edge_pct": math.nan, "breakeven_winrate": math.nan}
    q = 1.0 - p_win
    mu = per_trade_risk_dollar * (p_win * R - q)
    sigma = per_trade_risk_dollar * math.sqrt(p_win * q) * (R + 1.0)
    breakeven_p = 1.0 / (1.0 + R)
    return {
        "mu": mu,
        "sigma": sigma,
        "expectancy_R": p_win * R - q,           # in R-multiples
        "edge_pct": mu / per_trade_risk_dollar,  # μ as a % of risk
        "breakeven_winrate": breakeven_p,        # p* for zero expectancy at this R
    }


def _kelly_fraction(p_win: float, R: float) -> dict:
    """
    Kelly criterion for binary asymmetric payoff (Thorp 1969):
        f* = (p·R − q) / R          (fraction of bankroll to risk)

    Real-world: full Kelly maximises long-run geometric growth but suffers
    deep drawdowns (max DD ≈ ln(2)/μ_log under GBM). Practitioners use
    QUARTER Kelly (Bouchaud-Potters 2002, Haghani 2017) — gets ~94% of the
    growth with ~25% of the drawdown.
    """
    if not (0 < p_win < 1) or R <= 0:
        return {"full": math.nan, "half": math.nan, "quarter": math.nan}
    f_full = (p_win * R - (1 - p_win)) / R
    f_full = max(0.0, f_full)
    return {"full": f_full, "half": f_full / 2.0, "quarter": f_full / 4.0}


def _gamblers_ruin(p_win: float, R: float, per_trade_risk: float,
                   distance_to_ruin: float, distance_to_target: float
                   ) -> float:
    """
    P(reach −distance_to_ruin before +distance_to_target) for a random walk
    with per-trade drift μ and variance σ², via the Brownian-motion solution
    of the gambler's-ruin ODE:

        (σ²/2) u'' + μ u' = 0
        u(−A) = 1   (ruined),    u(+B) = 0  (target hit)
        ⇒  P(ruin | x=0) = (1 − exp(−2μB/σ²)) / (exp(2μA/σ²) − exp(−2μB/σ²))

    where μ, σ are PER-TRADE expectancy and std (in dollars), and A, B are
    the dollar distances to the respective barriers.

    Accurate for ≥ 20 trades; for fewer trades it underestimates ruin slightly
    because the discrete formula has additional variance.

    Returns NaN when inputs are degenerate.
    """
    if (not (0 < p_win < 1) or R <= 0 or per_trade_risk <= 0
            or distance_to_ruin <= 0 or distance_to_target <= 0):
        return math.nan
    stats = _expectancy_per_trade(p_win, R, per_trade_risk)
    mu, sigma = stats["mu"], stats["sigma"]
    if sigma <= 0:
        return 0.0 if mu > 0 else 1.0
    # Symmetric (no edge) closed form to avoid 0/0
    if abs(mu) < 1e-9:
        return distance_to_target / (distance_to_target + distance_to_ruin)
    z_T = 2.0 * mu * distance_to_target / (sigma ** 2)
    z_D = 2.0 * mu * distance_to_ruin   / (sigma ** 2)
    try:
        # Numerically stable: rescale to avoid e^(huge)
        if mu > 0:
            # P(ruin) ≈ exp(-2μA/σ²) for B≫A
            num = 1.0 - math.exp(-z_T)
            den = math.exp(z_D) - math.exp(-z_T)
        else:
            # negative drift: factor differently
            num = math.exp(z_T) - 1.0
            den = math.exp(z_D + z_T) - 1.0   # equivalent algebraic form
        if den <= 0:
            return 1.0 if mu < 0 else 0.0
        return float(max(0.0, min(1.0, num / den)))
    except OverflowError:
        return 0.0 if mu > 0 else 1.0


def _streak_ruin_prob(p_win: float, n_losses_to_ruin: int) -> float:
    """
    P(losing-streak of length ≥ n_losses_to_ruin within next 100 trades),
    assuming i.i.d. trades. Closed form via complement of run-of-losses
    not occurring (Feller Vol I, §XIII.7 — combinatorial).

    Simplified upper bound: P(streak ≥ k in N trials) ≤ (N − k + 1) × q^k.
    Returns the upper-bound — conservative for risk warnings.
    """
    if not (0 < p_win < 1) or n_losses_to_ruin <= 0:
        return math.nan
    q = 1.0 - p_win
    N = 100
    if n_losses_to_ruin > N:
        return float(q ** n_losses_to_ruin)
    return float(min(1.0, (N - n_losses_to_ruin + 1) * (q ** n_losses_to_ruin)))


def _days_to_recover(drawdown_dollar: float, mu_per_trade: float,
                     trades_per_day: float = 3.0) -> float:
    """
    Expected trading days to recover a drawdown given positive expectancy.
    Naïve: days = DD / (μ × trades_per_day). Returns +inf if μ ≤ 0.
    """
    if mu_per_trade <= 0 or trades_per_day <= 0:
        return float("inf")
    if drawdown_dollar <= 0:
        return 0.0
    return drawdown_dollar / (mu_per_trade * trades_per_day)


def _consistency_check(best_day_profit: float, total_profit: float,
                       consistency_pct: float) -> dict:
    """
    Most prop firms require: best_day / total_profit ≤ consistency_pct
    (Apex 50%, Topstep 50% eval, Lucid Pro 40% funded, Lucid Direct 20%).

    Returns the current ratio, the minimum total profit required to make the
    current best-day compliant, and a pass/fail flag.
    """
    if consistency_pct <= 0:
        return {"ratio": math.nan, "min_total_needed": math.nan, "passes": True,
                "additional_needed": 0.0}
    if best_day_profit <= 0 or total_profit <= 0:
        return {"ratio": 0.0, "min_total_needed": 0.0, "passes": True,
                "additional_needed": 0.0}
    ratio = best_day_profit / total_profit
    min_total = best_day_profit / consistency_pct
    return {
        "ratio":             ratio,
        "min_total_needed":  min_total,
        "passes":            ratio <= consistency_pct,
        "additional_needed": max(0.0, min_total - total_profit),
    }


if mode == "7. Prop Firm Risk Manager":
    st.subheader("Mode 7: Prop Firm Risk Manager")
    st.markdown(
        "Designed for **funded futures accounts** (Apex, Topstep, "
        "MyFundedFutures, Lucid). Pick your firm + account, set your risk "
        "rules and trader edge (win-rate × R), and the engine sizes every "
        "trade so you (a) **don't blow the eval**, (b) **survive long enough "
        "to take payouts**, and (c) **stay compliant with the consistency "
        "rule**.\n\n"
        "_Rules are simplified per public firm docs — always verify with your "
        "firm's current rulebook before trading._"
    )

    # ── Firm + account setup ──────────────────────────────────────────────────
    pcol1, pcol2, pcol3 = st.columns(3)
    with pcol1:
        firm = st.selectbox("Prop firm", list(_PROP_FIRMS.keys()))
        # Lucid Direct is instant-funded, has no Eval phase
        phase_opts = (["Funded"] if "Direct" in firm
                      else ["Eval", "Funded"])
        phase = st.radio("Phase", phase_opts, horizontal=True)
    with pcol2:
        size_options = list(_PROP_FIRMS[firm].keys())
        size = st.selectbox("Account size ($)", size_options,
                            format_func=lambda x: f"${x:,}")
        instrument = st.selectbox(
            "Primary instrument",
            list(_TICK_INFO.keys()),
            format_func=lambda x: f"{x} — {_TICK_INFO[x]['label']}",
            index=0,
        )
    with pcol3:
        st.markdown("**Risk per trade**")
        risk_mode = st.radio("Risk style", ["% of account", "Fixed $"],
                             horizontal=True, label_visibility="collapsed")
        if risk_mode == "% of account":
            risk_pct = st.slider(
                "Risk % per trade", 0.1, 5.0, 0.5, 0.1,
                help="Pros: ≤1% per trade. Eval blow-ups usually start at >2%.",
            )
            risk_dollar_input = 0.0   # avoid NameError if user toggles modes
        else:
            risk_dollar_input = st.number_input(
                "Risk per trade ($)", value=100.0, step=25.0, min_value=10.0)
            risk_pct = 0.0

    (profit_target, trail_dd, daily_dd, max_n, win_days_req, total_days_req,
     buffer_, consistency_pct, trail_lock_at, trail_lock_floor) = _PROP_FIRMS[firm][size]

    # ── Trader edge (NEW: drives expectancy + gambler's ruin + Kelly) ─────────
    st.divider()
    st.markdown("#### Trader Edge (drives expectancy, Kelly & ruin probability)")
    ecol1, ecol2, ecol3 = st.columns(3)
    with ecol1:
        win_rate = st.slider(
            "Win rate (%)", 20, 80, 45, 1,
            help="Your historical % of profitable trades. Be honest — 45% "
                 "is a realistic discretionary intraday futures number.",
        ) / 100.0
    with ecol2:
        R_ratio = st.slider(
            "Avg win / avg loss (R)", 0.5, 5.0, 1.5, 0.1,
            help="Average winner divided by average loser, in stop units. "
                 "2.0 means you make 2× your stop on winners.",
        )
    with ecol3:
        trades_per_day = st.slider("Trades per day", 1, 20, 4, 1,
                                   help="Used to estimate days to recover, time-to-target.")

    # ── Account-state inputs ──────────────────────────────────────────────────
    st.divider()
    st.markdown("#### Current Account State")
    acol1, acol2, acol3, acol4 = st.columns(4)
    with acol1:
        cur_balance = st.number_input(
            "Current balance ($)", value=float(size), step=100.0, min_value=0.0,
            help="Today's account balance shown on your prop dashboard.")
    with acol2:
        # BUG B2 FIX: peak ≥ max(starting size, current balance)
        cur_peak = st.number_input(
            "Account peak ($, all-time high)",
            value=max(float(size), float(cur_balance)),
            step=100.0,
            min_value=max(float(size), float(cur_balance)),
            help="Highest balance the account has ever shown. Must be ≥ "
                 "current balance (mathematically). Trailing DD ratchets off this.")
    with acol3:
        today_pnl = st.number_input(
            "Today's P&L ($)", value=0.0, step=50.0,
            help="Realised + open P&L since the 5pm ET reset.")
    with acol4:
        stop_ticks = st.number_input(
            "Stop loss (ticks)", value=20, min_value=1,
            help=f"Your typical stop in {instrument} ticks. "
                 f"1 tick = {_TICK_INFO[instrument]['tick_size']}.")

    # Payout + consistency inputs (funded phase only)
    winning_days_done = total_days_done = best_day_pnl = total_profit_since_payout = 0
    if phase == "Funded":
        st.markdown("#### Payout & Consistency Inputs")
        pc1, pc2, pc3, pc4 = st.columns(4)
        with pc1:
            winning_days_done = st.number_input(
                "Winning days since last payout", value=0, min_value=0,
                help="Days closing ≥ $200 profit (firm rule).")
        with pc2:
            total_days_done = st.number_input(
                "Total trading days since last payout", value=0, min_value=0)
        with pc3:
            best_day_pnl = st.number_input(
                "Best single day profit ($)", value=0.0, min_value=0.0, step=50.0,
                help="Largest single-day green P&L in the current payout cycle.")
        with pc4:
            total_profit_since_payout = st.number_input(
                "Total profit since last payout ($)", value=0.0,
                min_value=0.0, step=100.0,
                help="Sum of all daily green P&L in the current payout cycle.")

    # ── Core calculations ────────────────────────────────────────────────────
    tick_value = _TICK_INFO[instrument]["tick_value"]
    risk_dollar = (cur_balance * risk_pct / 100.0
                   if risk_mode == "% of account" else risk_dollar_input)
    n_contracts = _round_contracts(risk_dollar, stop_ticks, tick_value, max_n)
    actual_per_trade_loss = max(n_contracts, 1) * stop_ticks * tick_value
    # Trail floor: defaults to dynamic (peak − trail_dd). For firms with a
    # documented "lock" rule (Apex 4.0, Topstep, MFFU, all Lucid tiers),
    # once peak reaches trail_lock_at the floor becomes a fixed dollar amount
    # (trail_lock_floor — usually the initial deposit).
    trail_floor = cur_peak - trail_dd
    locked = False
    if trail_lock_at is not None and cur_peak >= trail_lock_at and trail_lock_floor is not None:
        trail_floor = float(trail_lock_floor)
        locked = True

    # BUG B3 FIX: explicit None check, not falsy. (daily_dd can legitimately be 0
    # for the LucidFlex tier; treat 0 as "no daily DD" but distinguish from None.)
    has_daily_dd = (daily_dd is not None and daily_dd > 0)
    daily_floor = ((cur_balance - today_pnl) - daily_dd) if has_daily_dd else None

    cushion_trail = cur_balance - trail_floor
    cushion_daily = (cur_balance - daily_floor) if daily_floor is not None else None
    max_addl_loss = min(cushion_trail,
                        cushion_daily if cushion_daily is not None else cushion_trail)
    profit_to_target = (size + profit_target) - cur_balance if profit_target > 0 else 0.0

    # ── Edge math: expectancy / Kelly / ruin ─────────────────────────────────
    per_trade_risk_basis = stop_ticks * tick_value     # 1-contract stop $
    exp_stats = _expectancy_per_trade(win_rate, R_ratio, actual_per_trade_loss)
    kelly = _kelly_fraction(win_rate, R_ratio)
    # Probability of busting eval before passing:
    p_bust_eval = _gamblers_ruin(
        win_rate, R_ratio, actual_per_trade_loss,
        distance_to_ruin=max(cushion_trail, 1.0),
        distance_to_target=max(profit_to_target, 1.0),
    ) if (phase == "Eval" and profit_to_target > 0) else math.nan
    # Probability of blowing daily-DD today (if applicable):
    if has_daily_dd and cushion_daily and cushion_daily > 0:
        # Distance "to target" for today = a typical 5R day (rough proxy)
        p_bust_today = _gamblers_ruin(
            win_rate, R_ratio, actual_per_trade_loss,
            distance_to_ruin=cushion_daily,
            distance_to_target=5 * actual_per_trade_loss,
        )
    else:
        p_bust_today = math.nan
    # Losing-streak ruin (clearer when win rate is the only input)
    losses_to_breach_trail = int(cushion_trail // actual_per_trade_loss) if actual_per_trade_loss > 0 else 0
    losses_to_breach_daily = (int(cushion_daily // actual_per_trade_loss)
                              if (cushion_daily is not None and actual_per_trade_loss > 0)
                              else None)
    p_streak_trail = _streak_ruin_prob(win_rate, max(losses_to_breach_trail, 1))
    # Recovery time
    cur_drawdown = max(cur_peak - cur_balance, 0)
    days_recover = _days_to_recover(cur_drawdown, exp_stats["mu"], trades_per_day)
    # Consistency
    cons = _consistency_check(best_day_pnl, total_profit_since_payout, consistency_pct)

    # ── Output: Position Sizing ──────────────────────────────────────────────
    st.divider()
    st.markdown("#### Position Sizing")
    sz_cols = st.columns(4)
    with sz_cols[0]:
        st.metric("Risk / trade", f"${risk_dollar:,.0f}",
                  f"{risk_dollar/max(cur_balance,1)*100:.2f}% of balance")
    with sz_cols[1]:
        st.metric("Stop in $ (1 contract)", f"${per_trade_risk_basis:,.2f}",
                  f"{stop_ticks} ticks × ${tick_value}/tick")
    with sz_cols[2]:
        actual_risk = n_contracts * stop_ticks * tick_value
        st.metric("Contracts", f"{n_contracts}",
                  f"actual risk: ${actual_risk:,.0f}")
    with sz_cols[3]:
        if n_contracts == 0:
            st.metric("STATUS", " STOP TOO WIDE",
                      f"need stop ≤ {int(risk_dollar // tick_value)} ticks")
        elif n_contracts >= max_n:
            st.metric("STATUS", " AT MAX SIZE",
                      f"firm cap = {max_n} contracts")
        else:
            st.metric("STATUS", " WITHIN LIMITS", "")

    # Kelly comparison
    if not math.isnan(kelly["full"]):
        kelly_qtr_dollar = kelly["quarter"] * cur_balance
        kelly_full_dollar = kelly["full"] * cur_balance
        kc1, kc2, kc3 = st.columns(3)
        with kc1:
            st.metric("Full Kelly", f"{kelly['full']*100:.1f}%",
                      f"${kelly_full_dollar:,.0f} per trade")
        with kc2:
            st.metric("Quarter Kelly (recommended)", f"{kelly['quarter']*100:.2f}%",
                      f"${kelly_qtr_dollar:,.0f} per trade")
        with kc3:
            current_f = risk_dollar / max(cur_balance, 1)
            ratio = current_f / max(kelly["quarter"], 1e-9)
            if math.isnan(current_f) or kelly["quarter"] == 0:
                kelly_status = "n/a"
            elif ratio > 4:
                kelly_status = f" {ratio:.1f}× quarter-Kelly — OVER-SIZED"
            elif ratio > 1:
                kelly_status = f" {ratio:.1f}× quarter-Kelly — aggressive"
            else:
                kelly_status = f" {ratio:.2f}× quarter-Kelly — safe"
            st.metric("Your current size vs ¼ Kelly", kelly_status, "")

    # ── Output: Expectancy panel (NEW) ───────────────────────────────────────
    st.markdown("#### Trader Edge — Expectancy & Probability")
    if math.isnan(exp_stats["mu"]):
        st.info("Set a valid win-rate and R to see expectancy / ruin probabilities.")
    else:
        ec1, ec2, ec3, ec4 = st.columns(4)
        with ec1:
            st.metric("Expectancy / trade",
                      f"${exp_stats['mu']:+,.2f}",
                      f"{exp_stats['expectancy_R']*100:+.1f}% of risk per trade")
        with ec2:
            st.metric("Breakeven win rate (at this R)",
                      f"{exp_stats['breakeven_winrate']*100:.1f}%",
                      f"yours: {win_rate*100:.0f}%  ({'' if win_rate > exp_stats['breakeven_winrate'] else ' NEG EDGE'})")
        with ec3:
            # Daily ruin
            if not math.isnan(p_bust_today):
                color = "inverse" if p_bust_today > 0.10 else "off" if p_bust_today > 0.03 else "normal"
                st.metric("P(blow daily-DD today)",
                          f"{p_bust_today*100:.1f}%",
                          delta_color=color,
                          delta=f"streak ≥ {losses_to_breach_daily} losses ruins it")
            else:
                st.metric("P(losing streak ruins trail-DD)",
                          f"{p_streak_trail*100:.1f}% in 100 trades",
                          f"need {losses_to_breach_trail+1}+ losses in a row")
        with ec4:
            # Recovery time
            if not math.isinf(days_recover):
                st.metric(f"Days to recover ${cur_drawdown:,.0f} DD",
                          f"~{days_recover:.1f} days",
                          f"at {trades_per_day}/day × ${exp_stats['mu']:.2f}/trade")
            else:
                st.metric("Days to recover DD", "n/a",
                          "non-positive expectancy — find an edge first")

    # ── Output: Drawdown Status (BUG B1 FIXED) ───────────────────────────────
    st.markdown("#### Drawdown Status")
    dd_cols = st.columns(3 if has_daily_dd else 2)
    with dd_cols[0]:
        pct_used = (cur_peak - cur_balance) / trail_dd * 100 if trail_dd > 0 else 0
        delta_str = f"${cushion_trail:,.0f} cushion remaining"
        delta_color = ("inverse" if cushion_trail < trail_dd * 0.20
                       else "off" if cushion_trail < trail_dd * 0.50
                       else "normal")
        st.metric(
            f"Trailing DD ({'LOCKED static' if locked else 'dynamic'})",
            f"${trail_floor:,.0f} floor",
            delta_str,
            delta_color=delta_color,
        )
        st.progress(min(max(pct_used, 0), 100) / 100,
                    text=f"{pct_used:.0f}% of trail DD used (peak ${cur_peak:,.0f})")
    if has_daily_dd:
        with dd_cols[1]:
            day_used = -today_pnl / daily_dd * 100 if today_pnl < 0 else 0
            delta_color = ("inverse" if cushion_daily < daily_dd * 0.20
                           else "off" if cushion_daily < daily_dd * 0.50
                           else "normal")
            st.metric(
                "Daily DD",
                f"${daily_floor:,.0f} floor",
                f"${cushion_daily:,.0f} cushion (today P&L ${today_pnl:+,.0f})",
                delta_color=delta_color,
            )
            st.progress(min(max(day_used, 0), 100) / 100,
                        text=f"{day_used:.0f}% of daily DD used")
    with dd_cols[-1]:
        # BUG B1 FIX: divide by ACTUAL per-trade loss (with sizing), not 1-contract stop
        per_trade_loss_real = max(n_contracts, 1) * stop_ticks * tick_value
        max_trades = int(max_addl_loss // per_trade_loss_real) if per_trade_loss_real > 0 else 0
        st.metric("Trades until breach",
                  f"{max_trades}",
                  f"at ${per_trade_loss_real:,.0f}/loss × {max(n_contracts,1)} contract(s)")

    # ── Phase-specific panel ─────────────────────────────────────────────────
    if phase == "Eval":
        st.markdown("#### Eval Progress")
        ev_cols = st.columns(3)
        with ev_cols[0]:
            pct_target = (cur_balance - size) / profit_target * 100 if profit_target > 0 else 100
            st.metric("Profit target",
                      f"${size + profit_target:,.0f}",
                      f"${profit_to_target:,.0f} to go ({pct_target:.0f}% done)",
                      delta_color="normal" if profit_to_target > 0 else "inverse")
            st.progress(min(max(pct_target, 0), 100) / 100)
        with ev_cols[1]:
            # Days to pass using ACTUAL expectancy, not arbitrary 1R/day proxy
            if not math.isnan(exp_stats["mu"]) and exp_stats["mu"] > 0:
                days_per_target = profit_to_target / (exp_stats["mu"] * trades_per_day)
                st.metric("Days to pass @ current edge",
                          f"{max(days_per_target, 0):.1f} days",
                          f"at ${exp_stats['mu']:.2f}/trade × {trades_per_day}/day")
            else:
                st.metric("Days to pass", "n/a",
                          "expectancy ≤ 0 — never passes at current edge")
        with ev_cols[2]:
            # NEW: gambler's-ruin probability of passing
            if not math.isnan(p_bust_eval):
                p_pass = 1.0 - p_bust_eval
                color = "normal" if p_pass > 0.7 else "off" if p_pass > 0.4 else "inverse"
                st.metric("P(pass eval before busting)",
                          f"{p_pass*100:.1f}%",
                          f"P(bust trail-DD first): {p_bust_eval*100:.1f}%",
                          delta_color=color)
            else:
                st.metric("P(pass eval)", "n/a",
                          "set edge to compute")
        st.info(
            "**Eval-pass discipline:** keep risk at or under quarter-Kelly. "
            "Most evals fail not from bad trades but from oversizing after a "
            "loss to 'win it back.' If today's P&L is red ≥ 2× your per-trade "
            "risk — STOP TRADING TODAY. Sleep on it."
        )
    else:
        st.markdown("#### Payout Progress")
        po_cols = st.columns(3)
        with po_cols[0]:
            days_to_win = max(win_days_req - winning_days_done, 0)
            st.metric("Winning days remaining",
                      f"{days_to_win}",
                      f"{winning_days_done}/{win_days_req} done")
        with po_cols[1]:
            days_to_total = max(total_days_req - total_days_done, 0)
            st.metric("Total trading days remaining",
                      f"{days_to_total}",
                      f"{total_days_done}/{total_days_req} done")
        with po_cols[2]:
            safe_floor = trail_floor + buffer_
            payable_now = max(cur_balance - safe_floor, 0)
            st.metric("Safe payout amount",
                      f"${payable_now:,.0f}",
                      f"keeps ${safe_floor:,.0f} floor")
        eligible = (winning_days_done >= win_days_req
                    and total_days_done >= total_days_req
                    and payable_now > 0)
        if eligible:
            st.success(
                f" **PAYOUT ELIGIBLE.** Request up to **${payable_now:,.0f}** today. "
                f"Account stays at ${cur_balance - payable_now:,.0f} (cushion "
                f"${(cur_balance - payable_now) - trail_floor:,.0f} above trail floor)."
            )
        else:
            missing = []
            if winning_days_done < win_days_req:
                missing.append(f"{win_days_req - winning_days_done} more winning days (≥$200)")
            if total_days_done < total_days_req:
                missing.append(f"{total_days_req - total_days_done} more total trading days")
            if payable_now <= 0:
                missing.append(f"balance must exceed trail floor + ${buffer_} buffer")
            st.warning(" Not yet eligible — need: " + ", ".join(missing))

    # ── NEW: Consistency rule panel ──────────────────────────────────────────
    if consistency_pct > 0 and (phase == "Funded" or "Direct" in firm):
        st.markdown(f"#### Consistency Rule ({consistency_pct*100:.0f}% best-day cap)")
        if best_day_pnl <= 0 and total_profit_since_payout <= 0:
            st.info(
                f"Fill in **Best single day profit** and **Total profit** above to "
                f"check compliance. Rule: best day ≤ {consistency_pct*100:.0f}% of total."
            )
        else:
            cs1, cs2, cs3 = st.columns(3)
            with cs1:
                st.metric("Current best-day ratio",
                          f"{cons['ratio']*100:.1f}%",
                          f"limit: {consistency_pct*100:.0f}%",
                          delta_color="normal" if cons['passes'] else "inverse")
            with cs2:
                st.metric("Min total profit needed",
                          f"${cons['min_total_needed']:,.0f}",
                          f"you have: ${total_profit_since_payout:,.0f}")
            with cs3:
                if cons['passes']:
                    st.success(f" Compliant — payout allowed")
                else:
                    st.error(
                        f" Need **+${cons['additional_needed']:,.0f}** more total "
                        f"profit on OTHER days (or your next best day must drop) "
                        f"to satisfy the {consistency_pct*100:.0f}% rule."
                    )

    # ── Pre-trade checklist (BUG B4 FIXED) ───────────────────────────────────
    st.divider()
    st.markdown("#### Pre-Trade Checklist (read EVERY trade)")
    cushion_pct = cushion_trail / trail_dd * 100 if trail_dd > 0 else 100
    daily_used_pct = (-today_pnl / daily_dd * 100) if (has_daily_dd and today_pnl < 0) else 0
    checks = [
        ("" if n_contracts > 0 else "",
         f"Sizing computed: **{n_contracts} contracts** at ${actual_risk:,.0f} risk"),
        ("" if cushion_pct > 50 else "" if cushion_pct > 20 else "",
         f"Trail-DD cushion: **{cushion_pct:.0f}%** of buffer remaining (${cushion_trail:,.0f})"),
    ]
    if has_daily_dd:
        checks.append((
            "" if daily_used_pct < 50 else "" if daily_used_pct < 80 else "",
            f"Daily-DD used today: **{daily_used_pct:.0f}%** "
            f"(${-today_pnl if today_pnl < 0 else 0:,.0f} of ${daily_dd:,.0f})",
        ))
    # BUG B4 FIX: threshold uses risk_dollar (which scales with account size),
    # not just stop_ticks × tick_value (which is a 1-contract figure).
    if today_pnl < -2 * risk_dollar:
        checks.append(("",
            f" Today's P&L is **{today_pnl/max(risk_dollar,1):.1f}R underwater**. "
            "Stop trading. Most blown evals start with 'I'll make it back.'"))
    if cushion_pct < 20:
        checks.append(("",
            "DEFENSIVE MODE — within 20% of trail-DD floor. "
            "Halve risk per trade until cushion recovers above 50%."))
    # NEW edge-based checks
    if not math.isnan(exp_stats["mu"]) and exp_stats["mu"] <= 0:
        checks.append(("",
            f"NEGATIVE EXPECTANCY at win-rate {win_rate*100:.0f}% × R {R_ratio:.1f}. "
            f"You need win-rate > {exp_stats['breakeven_winrate']*100:.1f}% at this R, "
            "or a wider target. **Don't trade real money until edge is positive.**"))
    if not math.isnan(kelly["quarter"]) and kelly["quarter"] > 0:
        current_f = risk_dollar / max(cur_balance, 1)
        if current_f > 4 * kelly["quarter"]:
            checks.append(("",
                f"Sized at {current_f/kelly['quarter']:.1f}× quarter-Kelly — "
                "estimation error in win-rate can blow you up. Cut risk in half."))
    if not math.isnan(p_bust_eval) and p_bust_eval > 0.30:
        checks.append(("",
            f"At current sizing + edge, **P(bust eval) = {p_bust_eval*100:.0f}%**. "
            "Reduce risk per trade or widen R-targets."))

    for icon, txt in checks:
        st.markdown(f"{icon} {txt}")

    st.caption(
        "**Math used:** fixed-fractional sizing (Vince 1990 / Tharp 2008); "
        "Kelly criterion (Thorp 1969, Bouchaud-Potters 2002); gambler's-ruin "
        "from the Brownian-motion ODE — closed-form first-passage with two "
        "absorbing barriers (Karatzas-Shreve 1991, §2.6). Per-trade variance "
        "from binary-payoff identity σ = L·√(pq)·(R+1). Recovery time = "
        "DD ÷ (μ × trades/day). Consistency-rule and trail-lock parameters "
        "from each firm's public rulebook (Apex 4.0 §Reset Rules, Topstep "
        "Standard Combine Rules, MFFU Trader Manual, Lucid LucidFlex/Pro/"
        "Direct rule pages, all 2026 versions)."
    )


# ─── Results display ───────────────────────────────────────────────────────────

if st.session_state.cli_output:
    st.divider()

    output_text = st.session_state.cli_output

    # Detect and surface rate-limit errors prominently
    if "RATE LIMIT HIT" in output_text or "Too Many Requests" in output_text:
        st.error(
            "**Rate Limit Hit (Yahoo Finance)**\n\n"
            "Yahoo Finance is blocking requests from this server. "
            "Wait 60–120 seconds and click Run again, or reduce the number of stocks scanned."
        )

    st.subheader("Console Output")
    st.text(output_text)

if st.session_state.df_result is not None:
    st.divider()
    st.subheader("Trade Results Table")
    st.dataframe(st.session_state.df_result, width='stretch')

    # Download button for CSV
    csv = st.session_state.df_result.to_csv(index=False)
    st.download_button(
        label=" Download as CSV",
        data=csv,
        file_name="options_results.csv",
        mime="text/csv",
    )


# ─── AI Synthesis ──────────────────────────────────────────────────────────────

if st.session_state.cli_output:
    st.divider()
    st.subheader(" AI Synthesis")
    st.markdown(
        "Click below to send the console output to an LLM API for plain-English analysis. "
        "Requires an API key in the sidebar."
    )

    if st.button("Synthesize Output"):
        if not api_key:
            st.error("Enter your API Key in the sidebar first.")
        else:
            with st.spinner(f"Generating insights using {selected_model}…"):
                text_to_analyze = st.session_state.cli_output[-4000:]
                prompt = f"""
You are a senior quantitative options analyst. Analyze the following output from a Python options analysis engine.

```
{text_to_analyze}
```

Provide:
1. A concise summary (2–3 sentences) of what the data shows.
2. The single most actionable trade or takeaway from this output.
3. The top 2 risks associated with acting on this data.
Keep your response under 300 words. Be direct and specific — reference actual numbers from the output.
"""
                payload = {
                    "model":    selected_model,
                    "messages": [
                        {"role": "system",
                         "content": "You are a senior quantitative options analyst providing actionable insights."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.5,
                    "max_tokens":  600,
                }
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                }
                try:
                    ai_res = requests.post(
                        f"{api_base_url.rstrip('/')}/chat/completions",
                        headers=headers, json=payload, timeout=30,
                    )
                    if ai_res.status_code == 200:
                        ai_text = ai_res.json()["choices"][0]["message"]["content"]
                        st.markdown(ai_text)
                    elif ai_res.status_code == 429:
                        st.error("LLM API rate limit hit. Try again in a moment.")
                    else:
                        st.error(f"API Error {ai_res.status_code}: {ai_res.text[:200]}")
                except requests.Timeout:
                    st.error("LLM API timed out. Try again.")
                except Exception as e:
                    st.error(f"Failed to connect to LLM API: {e}")
