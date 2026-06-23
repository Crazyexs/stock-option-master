"""
flow_core.py — options FLOW and SKEW analytics from data the GEX engine already
downloads.
================================================================================
Pure-logic module (no Streamlit side effects). The GEX engine in `gex_core` only
reads `open_interest` + `gamma` from the CBOE payload, but each option record
also carries today's `volume`, `last_trade_price`, `theo`, `delta`, `iv` and a
`tick` (up/down) aggressor hint. Those fields are the *flow* — what is trading
RIGHT NOW — which is exactly what open-interest GEX cannot see (OI only updates
after the close, so intraday the walls are yesterday's book).

WHAT THIS ADDS (and why it matters)
-----------------------------------
1.  PUT/CALL RATIOS — both volume (today's flow) and open interest (standing
    positioning). A volume PCR spiking well above its OI PCR means fresh hedging
    / fear is hitting the tape faster than the book has built; a low PCR with
    rising call volume is chase/greed. This is the oldest sentiment gauge on the
    desk, and it costs nothing extra to compute.

2.  NET PREMIUM ($) — Σ(call volume × price × 100) − Σ(put volume × price × 100).
    Unlike contract counts this weights by the DOLLARS changing hands, so one
    fat ATM print outweighs a thousand penny wings. Positive = net call premium
    bought (bullish lean); negative = net put premium (bearish / hedging).

3.  FLOW GEX vs OI GEX — the same dealer-sign dollar-gamma as gex_core, but
    weighted by today's VOLUME instead of OI. Comparing the two tells you whether
    today's flow is REINFORCING the standing gamma wall (volume piling into the
    same strikes → wall hardens) or FIGHTING it (volume on the other side → wall
    is being eaten and is more likely to fail).

4.  SKEW / RISK-REVERSAL — using CBOE's per-contract delta we read IV at the
    25-delta put and 25-delta call of the front expiry:
        RR25 = IV(25d call) − IV(25d put)   (negative = the usual fear skew;
                                             a steepening put skew = rising fear)
        BF25 = mean(IV 25d wings) − ATM IV  (the smile's curvature / tail bid)
    Skew steepening often precedes vol expansion even when ATM IV is calm, so it
    is a leading-edge fear gauge that complements the GEX regime.

HONEST LIMITATIONS
------------------
* CBOE's CDN is ~15 min delayed; `volume` is cumulative for the session, not a
  live trade feed, so this is "flow so far today", not tick data.
* The `tick` up/down flag is a weak aggressor proxy (it is the last price tick,
  not a true trade-side classification), so the aggressor tilt is a hint, not a
  measured buy/sell imbalance.
* last_trade_price can be stale on illiquid strikes; we fall back to `theo` and
  only count premium where volume > 0 so dead strikes do not pollute the totals.
"""

from __future__ import annotations

from datetime import date as _date, datetime as _datetime

import pandas as pd

import gex_core as gx

_CM = 100.0

# CBOE symbol resolver. Index products need the leading underscore on the CDN;
# equities/ETFs do not. Extends gex_core's ES/NQ/GC map to anything tradable.
_INDEX_SYMBOLS = {"SPX", "NDX", "RUT", "VIX", "XSP", "DJX"}


def resolve_cboe(symbol: str) -> tuple[str, bool]:
    """(cboe_symbol, is_index) for a futures alias (ES/NQ/GC) or a raw ticker."""
    sym = symbol.upper().strip()
    if sym in gx._FUTURES_CBOE:                     # ES/NQ/GC -> SPX/NDX/GLD
        return gx._FUTURES_CBOE[sym]
    return sym, sym in _INDEX_SYMBOLS


# ── Chain load (keeps the flow fields gex_core drops) ─────────────────────────
def load_flow_chain(cboe_sym: str, is_index: bool, strike_range: float = 0.12
                    ) -> dict:
    """
    Parse the CBOE chain keeping volume / premium / delta. Returns
    {df, spot, error}. `df` columns: strike, type, exp, oi, volume, iv, gamma,
    delta, price, is_0dte. Best-effort: never raises.
    """
    try:
        raw = gx.fetch_cboe_raw(cboe_sym, is_index)
        data = raw.get("data", {})
        spot = float(data.get("current_price") or data.get("close") or 0)
        if not spot:
            return {"df": pd.DataFrame(), "spot": 0.0, "error": "no spot from CBOE"}
        opts = data.get("options", [])
        if not opts:
            return {"df": pd.DataFrame(), "spot": spot, "error": "no options from CBOE"}
        today_str = _date.today().isoformat()
        rows = []
        for rec in opts:
            code = rec.get("option", "")
            try:
                i = next(j for j, c in enumerate(code) if c.isdigit())
                exp_str = f"20{code[i:i+2]}-{code[i+2:i+4]}-{code[i+4:i+6]}"
                otype = code[i + 6]
                K = float(code[i + 7:]) / 1000.0
            except Exception:
                continue
            if spot <= 0 or abs(K - spot) / spot > strike_range:
                continue
            oi = float(rec.get("open_interest") or 0)
            vol = float(rec.get("volume") or 0)
            if oi == 0 and vol == 0:
                continue
            last = float(rec.get("last_trade_price") or 0)
            theo = float(rec.get("theo") or 0)
            price = last if last > 0 else theo
            rows.append({
                "strike":  K,
                "type":    otype,
                "exp":     exp_str,
                "oi":      oi,
                "volume":  vol,
                "iv":      float(rec.get("iv") or 0),
                "gamma":   float(rec.get("gamma") or 0),
                "delta":   float(rec.get("delta") or 0),
                "price":   price,
                "tick":    rec.get("tick", ""),
                "is_0dte": exp_str == today_str,
            })
        df = pd.DataFrame(rows)
        return {"df": df, "spot": round(spot, 2), "error": None if not df.empty
                else "no valid strikes after filter"}
    except Exception as exc:
        return {"df": pd.DataFrame(), "spot": 0.0, "error": str(exc)}


# ── Flow summary ──────────────────────────────────────────────────────────────
def _ratio(p: float, c: float) -> float | None:
    return round(p / c, 2) if c > 0 else None


def flow_summary(df: pd.DataFrame, spot: float, scope: str = "all") -> dict:
    """
    Put/call ratios, net premium and a volume-flow read.
    scope: "all" (every expiry kept in df) or "0dte" (today's expiry only).
    """
    if df is None or df.empty:
        return {"error": "no data"}
    work = df[df["is_0dte"]] if scope == "0dte" else df
    if work.empty:
        return {"error": f"no {scope} data"}

    calls = work[work["type"] == "C"]
    puts = work[work["type"] == "P"]

    call_vol = float(calls["volume"].sum())
    put_vol = float(puts["volume"].sum())
    call_oi = float(calls["oi"].sum())
    put_oi = float(puts["oi"].sum())

    # Premium ($) = price × volume × contract multiplier, only where it traded.
    call_prem = float((calls["price"] * calls["volume"]).sum()) * _CM
    put_prem = float((puts["price"] * puts["volume"]).sum()) * _CM
    net_prem = call_prem - put_prem

    pcr_vol = _ratio(put_vol, call_vol)
    pcr_oi = _ratio(put_oi, call_oi)

    # Sentiment read from the volume PCR (classic desk thresholds).
    if pcr_vol is None:
        tone = "n/a"
    elif pcr_vol >= 1.2:
        tone = "fear / hedging (put-heavy)"
    elif pcr_vol <= 0.6:
        tone = "greed / chase (call-heavy)"
    else:
        tone = "balanced"

    # Aggressor tilt: volume on upticks vs downticks (weak proxy, hint only).
    up = float(work.loc[work["tick"] == "up", "volume"].sum())
    dn = float(work.loc[work["tick"] == "down", "volume"].sum())
    aggressor = ("buyers lifting offers" if up > dn * 1.15 else
                 "sellers hitting bids" if dn > up * 1.15 else "two-way")

    return {
        "scope": scope,
        "call_volume": call_vol, "put_volume": put_vol,
        "call_oi": call_oi, "put_oi": put_oi,
        "pcr_volume": pcr_vol, "pcr_oi": pcr_oi,
        "call_premium": call_prem, "put_premium": put_prem,
        "net_premium": net_prem,
        "premium_lean": "bullish" if net_prem > 0 else ("bearish" if net_prem < 0 else "flat"),
        "tone": tone,
        "aggressor": aggressor,
        "n_contracts": int(len(work)),
    }


# ── Flow GEX (volume-weighted) vs OI GEX ──────────────────────────────────────
def _weighted_gex(df: pd.DataFrame, spot: float, weight_col: str) -> pd.DataFrame:
    """Per-strike dealer dollar-gamma using `weight_col` (oi or volume) as size."""
    if df is None or df.empty:
        return pd.DataFrame()
    dollar = _CM * spot * spot * 0.01
    w = df.copy()
    w["g$"] = w["gamma"] * w[weight_col] * dollar
    calls = w[w["type"] == "C"].groupby("strike")["g$"].sum().rename("call_gex")
    puts = w[w["type"] == "P"].groupby("strike")["g$"].sum().rename("put_gex")
    agg = pd.concat([calls, puts], axis=1).fillna(0.0).reset_index()
    agg["net_gex"] = agg["call_gex"] - agg["put_gex"]
    return agg.sort_values("strike").reset_index(drop=True)


def flow_vs_oi_gex(df: pd.DataFrame, spot: float, scope: str = "0dte") -> dict:
    """
    Compare where TODAY'S FLOW is building gamma (volume-weighted) against the
    STANDING book (OI-weighted). Same dealer sign convention as gex_core.

    Returns the net GEX of each, the top flow strike, and a read of whether flow
    is reinforcing or fighting the OI wall.
    """
    if df is None or df.empty:
        return {"error": "no data"}
    work = df[df["is_0dte"]] if scope == "0dte" else df
    if work.empty:
        return {"error": f"no {scope} data"}

    oi_agg = _weighted_gex(work, spot, "oi")
    vol_agg = _weighted_gex(work, spot, "volume")
    if oi_agg.empty or vol_agg.empty:
        return {"error": "insufficient strikes"}

    oi_net = float(oi_agg["net_gex"].sum())
    vol_net = float(vol_agg["net_gex"].sum())

    # OI call wall (strongest standing resistance) and where flow concentrates.
    oi_wall = float(oi_agg.loc[oi_agg["call_gex"].idxmax(), "strike"]) \
        if (oi_agg["call_gex"] > 0).any() else None
    flow_top = float(vol_agg.loc[vol_agg["net_gex"].abs().idxmax(), "strike"]) \
        if not vol_agg.empty else None

    if oi_net == 0:
        relation = "n/a"
    elif (vol_net >= 0) == (oi_net >= 0):
        relation = "flow REINFORCES the standing gamma regime (wall hardening)"
    else:
        relation = "flow FIGHTS the standing gamma regime (wall under pressure)"

    return {
        "scope": scope,
        "oi_net_gex": oi_net,
        "flow_net_gex": vol_net,
        "oi_call_wall": oi_wall,
        "flow_top_strike": flow_top,
        "relation": relation,
        "oi_agg": oi_agg,
        "vol_agg": vol_agg,
    }


# ── Skew / risk-reversal ──────────────────────────────────────────────────────
def _nearest_by_delta(side: pd.DataFrame, target: float) -> dict | None:
    """Row whose delta is closest to `target` (CBOE delta is signed)."""
    if side is None or side.empty:
        return None
    s = side.copy()
    s = s[s["iv"] > 0]
    if s.empty:
        return None
    s["dd"] = (s["delta"] - target).abs()
    r = s.sort_values("dd").iloc[0]
    return {"strike": float(r["strike"]), "iv": float(r["iv"]),
            "delta": float(r["delta"])}


def _norm_iv(iv: float) -> float:
    """CBOE iv is a decimal fraction (0.18 = 18%); convert to vol points for
    display. Guard the rare case where a value already arrives in percent."""
    return iv * 100.0 if 0 < iv <= 5.0 else iv


def skew_summary(df: pd.DataFrame, spot: float, expiry: str | None = None) -> dict:
    """
    25-delta risk-reversal + butterfly + put-skew slope for one expiry (the
    front expiry by default). IVs reported in vol points (percent).
    """
    if df is None or df.empty:
        return {"error": "no data"}
    exps = sorted(df["exp"].unique())
    if not exps:
        return {"error": "no expiries"}
    exp = expiry if (expiry in exps) else exps[0]
    chain = df[df["exp"] == exp]
    calls = chain[chain["type"] == "C"]
    puts = chain[chain["type"] == "P"]

    atm = chain[abs(chain["strike"] - spot) / spot < 0.01]
    atm_iv = _norm_iv(float(atm["iv"].mean())) if not atm.empty and atm["iv"].mean() > 0 else None

    c25 = _nearest_by_delta(calls, 0.25)
    p25 = _nearest_by_delta(puts, -0.25)
    if not c25 or not p25:
        return {"error": "no 25-delta strikes (thin chain)", "expiry": exp,
                "atm_iv": atm_iv}

    iv_c25 = _norm_iv(c25["iv"])
    iv_p25 = _norm_iv(p25["iv"])
    rr25 = round(iv_c25 - iv_p25, 2)                # <0 = put skew (normal fear)
    bf25 = round((iv_c25 + iv_p25) / 2 - atm_iv, 2) if atm_iv else None

    if rr25 <= -3.0:
        skew_read = "steep put skew — elevated downside fear / crash bid"
    elif rr25 < -0.5:
        skew_read = "normal put skew"
    elif rr25 > 0.5:
        skew_read = "call skew — upside chase / squeeze positioning"
    else:
        skew_read = "flat skew — symmetric"

    return {
        "expiry": exp,
        "atm_iv": round(atm_iv, 2) if atm_iv else None,
        "iv_25d_call": round(iv_c25, 2), "iv_25d_put": round(iv_p25, 2),
        "strike_25d_call": c25["strike"], "strike_25d_put": p25["strike"],
        "rr25": rr25, "bf25": bf25,
        "skew_read": skew_read,
    }


# ── One-call convenience for a page ───────────────────────────────────────────
def analyze(symbol: str, strike_range: float = 0.12) -> dict:
    """Full flow + skew read for a symbol (ES/NQ/GC alias or raw CBOE ticker)."""
    cboe_sym, is_index = resolve_cboe(symbol)
    loaded = load_flow_chain(cboe_sym, is_index, strike_range=strike_range)
    if loaded.get("error") and (loaded["df"] is None or loaded["df"].empty):
        return {"symbol": symbol, "cboe": cboe_sym, "error": loaded["error"]}
    df, spot = loaded["df"], loaded["spot"]
    return {
        "symbol": symbol, "cboe": cboe_sym, "spot": spot,
        "flow_all": flow_summary(df, spot, "all"),
        "flow_0dte": flow_summary(df, spot, "0dte"),
        "flow_vs_oi": flow_vs_oi_gex(df, spot, "0dte"),
        "skew": skew_summary(df, spot),
        "expiries": sorted(df["exp"].unique()),
        "df": df,
    }
