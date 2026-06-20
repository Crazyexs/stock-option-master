"""
gex_core.py — Institutional-grade Gamma Exposure (GEX) math for day trading.
================================================================================

This is a clean, importable re-implementation of the GEX engine with the
formula corrections needed for *intraday* trading of ES / NQ / GC. It is used
by  pages/1__GEX_Day_Trade.py  and has **no Streamlit side effects** at import
time, so it is safe to import from anywhere.

WHAT "THE FORMULA THE BIG DESKS USE" ACTUALLY IS
------------------------------------------------
There is no single secret. Dealer-flow desks (SpotGamma, SqueezeMetrics, GS
derivatives strats) all compute the same three numbers; the edge is in getting
the *sign convention*, the *dollar weighting*, and the *spot at which gamma
nets to zero* right. This module fixes the three places the original app.py got
loose for day trading:

1.  DOLLAR GAMMA, not share gamma.
        GEX$_i = sign_i · Γ_i · OI_i · CM · S² · 0.01
    Γ·OI·100·S (the old code) is *share* gamma — the number of deltas created
    per $1 move. Desks quote **dollar gamma per 1% move**: multiply by S and by
    1% (= S·0.01). This is what tells you how many *dollars* of futures the
    dealer must buy/sell to stay hedged, i.e. the true strength of a wall.
    NOTE: this does NOT move the wall strikes or the flip (a positive constant
    can't change an argmax or a root) — it makes the magnitudes real so you can
    rank wall strength and read the regime size.

2.  CALL-ONLY / PUT-ONLY WALLS, not net-per-strike walls.
        Call Wall = strike with the largest *call* dollar-gamma  (≥ spot)
        Put  Wall = strike with the largest *put*  dollar-gamma  (≤ spot)
    The old code took argmax/argmin of the *net* signed gamma at each strike, so
    a strike with huge call AND put OI would cancel out and vanish — exactly the
    pin strikes that matter most intraday. Separating the books is the
    SpotGamma "Call Wall / Put Wall" definition and gives stable magnets.

3.  TRUE ZERO-GAMMA (gamma flip) by root-find, recomputing Γ at the trial spot.
        flip = S*  such that  Σ_i sign_i · Γ_i(S*) · OI_i = 0
    (Already correct in app.py via brentq; reproduced here.) Above the flip the
    dealer book is net-long gamma → price PINS / mean-reverts → fade extremes.
    Below the flip the book is net-short gamma → dealers chase → price TRENDS →
    trade momentum. This single line is the regime switch the whole desk keys
    off.

HONEST LIMITATIONS (read before risking money)
----------------------------------------------
* CBOE's free CDN is ~15 minutes delayed and **open interest only updates after
  the close**. So intraday the *walls* are yesterday's-close walls; only spot
  moves live. That is true of essentially every retail GEX tool — the walls are
  slow-moving levels, not tick data.
* The dealer-sign assumption (long calls / short puts) is well-validated for
  SPX/NDX/SPY index flow but is an assumption, not measured order flow.
* GEX levels are *where reactions are likely*, not a profit guarantee. Position
  size and stops still decide whether you make money. No formula removes risk.
"""

from __future__ import annotations

import math
from datetime import date as _date, datetime as _datetime

import requests
import pandas as pd

try:
    import pytz
    _ET = pytz.timezone("America/New_York")
except Exception:                                    # pragma: no cover
    _ET = None


# ── Universe ──────────────────────────────────────────────────────────────────
# fut -> (cboe_symbol, is_index)
_FUTURES_CBOE = {
    "ES": ("SPX", True),    # SPX index options  — same price scale as ES
    "NQ": ("NDX", True),    # NDX index options  — same price scale as NQ
    "GC": ("GLD", False),   # GLD ETF options    — scaled up to GC equivalent
}
_YF_FUTURES = {"ES": "ES=F", "NQ": "NQ=F", "GC": "GC=F"}

# Continuous dividend yield used in the Merton gamma / zero-gamma root finder.
_DIV_YIELD = {"ES": 0.013, "NQ": 0.007, "GC": 0.0}

# Risk-free rate (only enters d1; intraday sensitivity is negligible).
_RISK_FREE = 0.05

# Contract multiplier for index/ETF options.
_CM = 100.0

# 1-hour floor (in years) so a 0DTE option's gamma never blows up at T→0.
_MIN_T = 1.0 / 24.0 / 365.0

# Regular cash session length in minutes (09:30–16:00 ET).
_SESSION_MIN = 390.0


# ── Spot ──────────────────────────────────────────────────────────────────────
def fetch_yf_spot(ticker: str) -> float | None:
    """Latest futures price from yfinance (used as the live anchor + GC scale)."""
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="2d")
        return float(h["Close"].iloc[-1]) if not h.empty else None
    except Exception:
        return None


# ── Black–Scholes / Merton gamma ──────────────────────────────────────────────
def bs_gamma(S: float, K: float, T: float, sigma: float,
             r: float = _RISK_FREE, q: float = 0.0) -> float:
    """
    Merton (1973) gamma with continuous dividend yield q:
        d1 = [ln(S/K) + (r − q + σ²/2)·T] / (σ·√T)
        Γ  = e^(−qT) · φ(d1) / (S·σ·√T)
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return (math.exp(-q * T) * math.exp(-0.5 * d1 ** 2)
                / (math.sqrt(2 * math.pi) * S * sigma * math.sqrt(T)))
    except Exception:
        return 0.0


# ── CBOE chain ────────────────────────────────────────────────────────────────
def fetch_cboe_raw(sym: str, is_index: bool) -> dict:
    prefix = "_" if is_index else ""
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{prefix}{sym}.json"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_chain(opts: list, spot_raw: float, scale: float,
                strike_range: float = 0.10, q: float = 0.0) -> pd.DataFrame:
    """
    Parse the CBOE option list into a tidy frame, filling missing gamma with the
    Merton model. `strike_range` is the ± fraction of spot to keep (±10% default
    for day trading — far OTM strikes carry ~no gamma intraday and only add
    noise to the wall search).
    """
    today_str = _date.today().isoformat()
    rows = []
    for rec in opts:
        code = rec.get("option", "")
        try:
            i = next(j for j, c in enumerate(code) if c.isdigit())
            exp_str  = f"20{code[i:i+2]}-{code[i+2:i+4]}-{code[i+4:i+6]}"
            opt_type = code[i + 6]                       # 'C' or 'P'
            K_raw    = float(code[i + 7:]) / 1000.0
        except Exception:
            continue
        if spot_raw <= 0 or abs(K_raw - spot_raw) / spot_raw > strike_range:
            continue
        oi    = float(rec.get("open_interest") or 0)
        iv    = float(rec.get("iv")            or 0)
        gamma = float(rec.get("gamma")         or 0)
        if oi == 0:
            continue
        if gamma == 0 and iv > 0:
            try:
                exp_date = _datetime.strptime(exp_str, "%Y-%m-%d").date()
            except Exception:
                continue
            days = max((exp_date - _date.today()).days, 0)
            T = max(days / 365.0, _MIN_T)
            iv_dec = iv / 100.0 if iv > 1.0 else iv
            gamma = bs_gamma(spot_raw, K_raw, T, iv_dec, q=q)
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


# ── Dollar-gamma aggregation (FIX #1 + #2) ────────────────────────────────────
def aggregate(df: pd.DataFrame, spot: float) -> pd.DataFrame:
    """
    Per-strike DOLLAR gamma, split into call / put / net.
        gex$_i = Γ_i · OI_i · CM · S² · 0.01   (dealers buy/sell per 1% move)
    Returns columns: strike, call_gex, put_gex (>=0 magnitude), net_gex, cum_net.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    dollar = _CM * spot * spot * 0.01
    work = df.copy()
    work["gex"] = work["gamma"] * work["oi"] * dollar
    calls = (work[work["type"] == "C"].groupby("strike")["gex"].sum()
             .rename("call_gex"))
    puts  = (work[work["type"] == "P"].groupby("strike")["gex"].sum()
             .rename("put_gex"))
    agg = pd.concat([calls, puts], axis=1).fillna(0.0).reset_index()
    agg = agg.sort_values("strike").reset_index(drop=True)
    # Dealer sign convention: long calls (+), short puts (−).
    agg["net_gex"] = agg["call_gex"] - agg["put_gex"]
    agg["cum_net"] = agg["net_gex"].cumsum()
    return agg


# ── Second-order Greeks for exposure profiles (Vanna / Charm) ─────────────────
def _greeks_for_exposure(S: float, K: float, T: float, sigma: float,
                         otype: str, q: float = 0.0, r: float = _RISK_FREE
                         ) -> tuple[float, float, float]:
    """
    Return (gamma, vanna, charm_per_day) under Merton.
        vanna = ∂Δ/∂σ = −e^(−qT)·φ(d1)·d2/σ          (type-independent)
        charm = ∂Δ/∂t (Haug 2007), sign flips call↔put, scaled to per-CALENDAR-day
    These feed dealer VANNA and CHARM exposure, the two systematic *directional*
    intraday flows that pure gamma exposure cannot see (vanna rally / vol-linked
    bid; charm = the mechanical afternoon drift as 0DTE delta decays).
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return (0.0, 0.0, 0.0)
    try:
        srt = sigma * math.sqrt(T)
        d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / srt
        d2 = d1 - srt
        pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
        eqt = math.exp(-q * T)
        gamma = eqt * pdf / (S * srt)
        vanna = -eqt * pdf * d2 / sigma
        charm_call = -eqt * pdf * (2 * (r - q) * T - d2 * srt) / (2 * T * srt)
        charm = (charm_call if otype == "C" else -charm_call) / 365.0
        return (gamma, vanna, charm)
    except Exception:
        return (0.0, 0.0, 0.0)


def aggregate_exposures(df: pd.DataFrame, spot: float, q: float = 0.0) -> pd.DataFrame:
    """
    Per-strike DOLLAR exposure for the three desks key off, in one pass.
        net_gex = Σ sign · Γ · OI · CM · S² · 0.01   ($ hedge per 1% spot move)
        net_vex = Σ sign · Vanna · OI · CM · S       ($Δ change per 1.00 vol pt)
        net_cex = Σ sign · Charm · OI · CM · S        ($Δ drift per calendar day)
    sign = +1 for calls (dealer long), −1 for puts (dealer short) — same dealer
    convention as `aggregate`. call_gex / put_gex are kept as positive magnitudes
    so the wall search is unchanged. Superset of `aggregate`'s columns, so the
    chart and `_wall_strikes` work against this frame directly.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    today = _date.today()
    dollar_g = _CM * spot * spot * 0.01     # gamma → $ per 1% move
    dollar_l = _CM * spot                   # vanna/charm → $ delta
    recs = []
    for _, row in df.iterrows():
        try:
            K = float(row["strike"])
            exp_date = _datetime.strptime(row["exp"], "%Y-%m-%d").date()
            days = max((exp_date - today).days, 0)
            T = max(days / 365.0, _MIN_T)
        except Exception:
            continue
        iv = row.get("iv", 0) or 0
        iv_dec = iv / 100.0 if iv > 1.0 else iv
        if iv_dec <= 0:
            continue
        otype = row["type"]
        g, vanna, charm = _greeks_for_exposure(spot, K, T, iv_dec, otype, q=q)
        # Prefer CBOE's published gamma when present (matches `aggregate`).
        g_use = float(row.get("gamma", 0) or 0) or g
        oi = float(row["oi"])
        pos_sign = 1.0 if otype == "C" else -1.0
        gex_mag = g_use * oi * dollar_g
        recs.append({
            "strike":   K,
            "call_gex": gex_mag if otype == "C" else 0.0,
            "put_gex":  gex_mag if otype == "P" else 0.0,
            "net_gex":  pos_sign * gex_mag,
            "net_vex":  pos_sign * vanna * oi * dollar_l,
            "net_cex":  pos_sign * charm * oi * dollar_l,
        })
    if not recs:
        return pd.DataFrame()
    agg = (pd.DataFrame(recs).groupby("strike")
           .agg(call_gex=("call_gex", "sum"), put_gex=("put_gex", "sum"),
                net_gex=("net_gex", "sum"), net_vex=("net_vex", "sum"),
                net_cex=("net_cex", "sum"))
           .reset_index().sort_values("strike").reset_index(drop=True))
    agg["cum_net"] = agg["net_gex"].cumsum()
    return agg


# ── True zero-gamma spot (FIX #3) ─────────────────────────────────────────────
def _net_gamma_at(df_sub: pd.DataFrame, S_star: float, q: float) -> float:
    today = _date.today()
    total = 0.0
    for _, row in df_sub.iterrows():
        try:
            exp_date = _datetime.strptime(row["exp"], "%Y-%m-%d").date()
            days = max((exp_date - today).days, 0)
            T = max(days / 365.0, _MIN_T)
        except Exception:
            continue
        iv = row.get("iv", 0) or 0
        iv_dec = iv / 100.0 if iv > 1.0 else iv
        if iv_dec <= 0:
            continue
        g = bs_gamma(S_star, float(row["strike"]), T, iv_dec, q=q)
        if g == 0:
            continue
        sign = 1 if row["type"] == "C" else -1
        total += sign * g * row["oi"]
    return total


def zero_gamma_spot(df_sub: pd.DataFrame, spot: float, q: float = 0.0,
                    max_width: float = 0.10) -> float | None:
    """
    Root S* of net dealer gamma = 0 via Brent.

    IMPORTANT for day trading: a flip only matters if it is NEAR spot. If the
    dealer book doesn't change sign within ±max_width (default ±10%) of spot,
    there is no meaningful intraday regime pivot — the whole book is one-sided
    (strongly long- or short-gamma) and the regime is read off the *sign* of net
    GEX, not a far-away root. We therefore search progressively wider only up to
    max_width and return None beyond that, instead of reporting a tail root 25–40%
    away that would mislead (the original engine's bug).
    """
    if df_sub is None or df_sub.empty:
        return None
    try:
        from scipy.optimize import brentq
        f = lambda s: _net_gamma_at(df_sub, s, q)
        widths = [w for w in (0.03, 0.05, 0.08, 0.10, 0.15, 0.25, 0.40)
                  if w <= max_width + 1e-9] or [max_width]
        for width in widths:
            lo, hi = spot * (1 - width), spot * (1 + width)
            flo, fhi = f(lo), f(hi)
            if flo == 0:
                return lo
            if fhi == 0:
                return hi
            if flo * fhi < 0:
                root = float(brentq(f, lo, hi, xtol=max(spot * 0.0005, 0.01),
                                    maxiter=60))
                # Guard: only accept a root that is genuinely within the window.
                if abs(root - spot) / spot <= max_width + 1e-9:
                    return root
        return None
    except Exception:
        return None


# ── Intraday expected-move band ───────────────────────────────────────────────
def _session_fraction_remaining(now: _datetime | None = None) -> float:
    """Fraction of the RTH session left (1.0 pre-open, →0 into the close)."""
    if _ET is None:
        return 1.0
    now = now or _datetime.now(_ET)
    if now.tzinfo is None:
        now = _ET.localize(now)
    if now.weekday() >= 5:                       # weekend → next full session
        return 1.0
    open_t  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    if now <= open_t:
        return 1.0
    if now >= close_t:
        return 1.0                                # after close → next session
    remaining_min = (close_t - now).total_seconds() / 60.0
    return max(0.05, min(1.0, remaining_min / _SESSION_MIN))


def expected_move(df_sub: pd.DataFrame, spot: float, dte: int,
                  now: _datetime | None = None) -> tuple[float | None, float | None, float | None]:
    """
    1σ band from ATM IV. For 0DTE it scales by the fraction of the session that
    is left, so the band tightens into the close instead of quoting a full day.
    Returns (sigma_points, upper, lower).
    """
    if df_sub is None or df_sub.empty:
        return (None, None, None)
    atm = df_sub[abs(df_sub["strike"] - spot) / spot < 0.025]
    if atm.empty:
        return (None, None, None)
    atm_iv = atm["iv"].mean()
    if not atm_iv or atm_iv <= 0:
        return (None, None, None)
    iv_dec = atm_iv / 100.0 if atm_iv > 1.0 else atm_iv
    if dte <= 0:
        horizon = _session_fraction_remaining(now) / 252.0
    else:
        horizon = dte / 252.0
    sig = spot * iv_dec * math.sqrt(max(horizon, 1e-6))
    return (round(sig, 2), round(spot + sig, 2), round(spot - sig, 2))


# ── Wall extraction (FIX #2) ──────────────────────────────────────────────────
def _wall_strikes(agg: pd.DataFrame, spot: float) -> dict:
    """
    Call Wall  = strike at/above spot with the most CALL dollar-gamma (ceiling).
    Put  Wall  = strike at/below spot with the most PUT  dollar-gamma (floor).
    Secondary walls = the next-largest on each side. Falls back to the global
    max on a side if nothing sits the 'right' side of spot.
    """
    out = {"call_wall": None, "put_wall": None,
           "secondary_call_wall": None, "secondary_put_wall": None}
    if agg is None or agg.empty:
        return out

    calls_up = agg[agg["strike"] >= spot].sort_values("call_gex", ascending=False)
    if calls_up.empty or calls_up["call_gex"].iloc[0] <= 0:
        calls_up = agg.sort_values("call_gex", ascending=False)
    puts_dn = agg[agg["strike"] <= spot].sort_values("put_gex", ascending=False)
    if puts_dn.empty or puts_dn["put_gex"].iloc[0] <= 0:
        puts_dn = agg.sort_values("put_gex", ascending=False)

    if not calls_up.empty and calls_up["call_gex"].iloc[0] > 0:
        out["call_wall"] = float(calls_up["strike"].iloc[0])
        if len(calls_up) > 1 and calls_up["call_gex"].iloc[1] > 0:
            out["secondary_call_wall"] = float(calls_up["strike"].iloc[1])
    if not puts_dn.empty and puts_dn["put_gex"].iloc[0] > 0:
        out["put_wall"] = float(puts_dn["strike"].iloc[0])
        if len(puts_dn) > 1 and puts_dn["put_gex"].iloc[1] > 0:
            out["secondary_put_wall"] = float(puts_dn["strike"].iloc[1])
    return out


# ── Trade plan ────────────────────────────────────────────────────────────────
def build_plan(spot: float, flip: float | None, call_wall: float | None,
               put_wall: float | None, net_total: float) -> dict:
    """
    Translate the levels into a concrete intraday bias + entries.
    Regime is set by spot vs the zero-gamma flip:
      • spot > flip  → POSITIVE gamma → dealers fade → market PINS / mean-reverts
                       → sell rallies into call wall, buy dips into put wall.
      • spot < flip  → NEGATIVE gamma → dealers chase → market TRENDS
                       → trade breakouts; losses of put wall / breaks of call
                         wall accelerate.
    A wall within 0.15% of spot overrides with a reaction (fade) setup.
    """
    plan = {"regime": "n/a", "bias": "n/a", "playbook": [], "primary": None}
    if spot <= 0:
        plan["playbook"] = ["No spot price — cannot build a plan."]
        return plan

    # Regime: prefer spot vs the nearby zero-gamma flip. If there is no flip
    # near spot, the book is one-sided and the regime is the SIGN of net GEX
    # (net>0 → dealers net-long gamma → pin; net<0 → net-short → trend).
    if flip is not None:
        pos_gamma = spot >= flip
        flip_note = f"zero-gamma {flip:,.2f}"
    else:
        pos_gamma = net_total >= 0
        flip_note = ("no flip near spot — book is net-LONG gamma (pin)" if pos_gamma
                     else "no flip near spot — book is net-SHORT gamma (trend)")
    plan["regime"] = ("POSITIVE gamma (mean-revert / pin)" if pos_gamma
                      else "NEGATIVE gamma (trend / momentum)")
    flip_txt = f"{flip:,.2f}" if flip is not None else flip_note

    near = lambda lvl: lvl is not None and abs(spot - lvl) / spot < 0.0015
    if near(call_wall):
        plan["bias"] = "FADE SHORT at call wall"
        plan["primary"] = call_wall
        plan["playbook"] = [
            f"At call wall {call_wall:,.2f} — dealer selling caps price here.",
            f"Scalp SHORT rejection; stop just above wall (~{call_wall*1.0008:,.2f}).",
            f"Target {flip_txt}" + (f" then put wall {put_wall:,.2f}." if put_wall else "."),
        ]
        return plan
    if near(put_wall):
        plan["bias"] = "FADE LONG at put wall"
        plan["primary"] = put_wall
        plan["playbook"] = [
            f"At put wall {put_wall:,.2f} — dealer buying supports price here.",
            f"Scalp LONG bounce; stop just below wall (~{put_wall*0.9992:,.2f}).",
            f"Target {flip_txt}" + (f" then call wall {call_wall:,.2f}." if call_wall else "."),
        ]
        return plan

    if pos_gamma:
        plan["bias"] = "RANGE — fade the edges toward zero-gamma"
        plan["primary"] = flip if flip is not None else put_wall
        pb = [f"POSITIVE gamma ({flip_txt}): dealers dampen moves, expect chop / "
              "mean-reversion — fade the extremes."]
        if call_wall:
            pb.append(f"Sell rallies into call wall {call_wall:,.2f} (resistance).")
        if put_wall:
            pb.append(f"Buy dips into put wall {put_wall:,.2f} (support).")
        if flip is not None:
            pb.append(f"Lose {flip:,.2f} on a closing basis → regime flips to trend; drop the fade.")
        plan["playbook"] = pb
    else:
        plan["bias"] = "TREND — trade momentum / breakouts"
        plan["primary"] = flip if flip is not None else call_wall
        pb = [f"NEGATIVE gamma ({flip_txt}): dealers amplify moves, expect trend "
              "& expansion."]
        if put_wall:
            pb.append(f"Break of put wall {put_wall:,.2f} → accelerant lower, ride it.")
        if call_wall and flip is not None:
            pb.append(f"Reclaim {flip:,.2f} → squeeze toward call wall {call_wall:,.2f}.")
        elif call_wall:
            pb.append(f"Squeeze target is call wall {call_wall:,.2f}.")
        pb.append("Avoid fading; let winners run to the next wall.")
        plan["playbook"] = pb
    return plan


# ── Per-symbol day-trade computation ──────────────────────────────────────────
def _pick_daytrade_expiry(df: pd.DataFrame) -> tuple[pd.DataFrame, str, int]:
    """0DTE if today is an expiry; else nearest ≤5 DTE; else nearest. -> (df, exp, dte)."""
    today = _date.today()
    exps = sorted(df["exp"].unique())
    dt = df[df["is_0dte"]]
    if not dt.empty:
        return dt.copy(), today.isoformat(), 0
    for exp in exps:
        try:
            dte = (_datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        except Exception:
            continue
        if 0 <= dte <= 5:
            return df[df["exp"] == exp].copy(), exp, dte
    if exps:
        exp = exps[0]
        dte = (_datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        return df[df["exp"] == exp].copy(), exp, max(dte, 0)
    return pd.DataFrame(), "", 0


def compute_symbol(fut_sym: str, now: _datetime | None = None) -> dict:
    """Full day-trade GEX read for one futures symbol. Returns a flat dict."""
    cboe_sym, is_index = _FUTURES_CBOE[fut_sym]
    try:
        raw  = fetch_cboe_raw(cboe_sym, is_index)
        data = raw.get("data", {})
        spot_r = float(data.get("current_price") or data.get("close") or 0)
        if not spot_r:
            return {"symbol": fut_sym, "error": "No spot from CBOE"}

        if fut_sym == "GC":
            gc = fetch_yf_spot(_YF_FUTURES["GC"])
            scale = (gc / spot_r) if (gc and spot_r > 0) else 10.0
            spot = round(gc or spot_r * scale, 2)
        else:
            yf = fetch_yf_spot(_YF_FUTURES[fut_sym])
            spot = round(yf or spot_r, 2)
            scale = 1.0

        q = _DIV_YIELD.get(fut_sym, 0.0)
        opts = data.get("options", [])
        if not opts:
            return {"symbol": fut_sym, "error": "No options from CBOE"}

        df = parse_chain(opts, spot_r, scale, strike_range=0.10, q=q)
        if df.empty:
            return {"symbol": fut_sym, "error": "No valid strikes after filter"}

        df_dt, exp_used, dte = _pick_daytrade_expiry(df)
        if df_dt.empty:
            return {"symbol": fut_sym, "error": "No tradeable expiry ≤5 DTE"}

        agg = aggregate_exposures(df_dt, spot, q=q)
        walls = _wall_strikes(agg, spot)
        flip = zero_gamma_spot(df_dt, spot, q=q)
        net_total = float(agg["net_gex"].sum()) if not agg.empty else 0.0
        net_vex = float(agg["net_vex"].sum()) if not agg.empty else 0.0
        net_cex = float(agg["net_cex"].sum()) if not agg.empty else 0.0
        sig, upper, lower = expected_move(df_dt, spot, dte, now=now)
        plan = build_plan(spot, flip, walls["call_wall"], walls["put_wall"], net_total)

        # Wall strength as a share of that side's total $gamma, and distance to
        # each wall measured in 1σ units (can spot realistically reach it today?).
        cw, pw = walls["call_wall"], walls["put_wall"]
        tot_call = float(agg["call_gex"].sum()) if not agg.empty else 0.0
        tot_put = float(agg["put_gex"].sum()) if not agg.empty else 0.0
        def _strength(side_col, strike, total):
            if strike is None or total <= 0 or agg.empty:
                return None
            row = agg[agg["strike"] == strike]
            return round(float(row[side_col].iloc[0]) / total * 100.0, 1) if not row.empty else None
        def _dist_sigma(strike):
            if strike is None or not sig:
                return None
            return round((strike - spot) / sig, 2)
        # Charm drift: sign of net dealer charm → mechanical drift as 0DTE decays.
        # +ve → dealers buy into the close (positive-gamma ramp); −ve → sell-off.
        # EXPERIMENTAL — direction must be confirmed by the snapshot backtest.
        if abs(net_cex) < 1e-6:
            charm_drift = "flat"
        else:
            charm_drift = "up into close" if net_cex > 0 else "down into close"

        return {
            "symbol": fut_sym,
            "cboe": cboe_sym,
            "spot": spot,
            "spot_cboe": round(spot_r * scale, 2),
            "scale": round(scale, 4),
            "expiry": exp_used,
            "dte": dte,
            "is_0dte": dte == 0,
            "call_wall": walls["call_wall"],
            "put_wall": walls["put_wall"],
            "secondary_call_wall": walls["secondary_call_wall"],
            "secondary_put_wall": walls["secondary_put_wall"],
            "gamma_flip": round(flip, 2) if flip is not None else None,
            "net_gex": net_total,                 # $ per 1% move, +pin / −trend
            "net_vex": net_vex,                   # $Δ per 1.00 vol pt (vanna)
            "net_cex": net_cex,                   # $Δ per day (charm) — drift sign
            "charm_drift": charm_drift,           # mechanical 0DTE decay drift
            "call_wall_strength": _strength("call_gex", cw, tot_call),  # % of call $γ
            "put_wall_strength": _strength("put_gex", pw, tot_put),     # % of put $γ
            "call_wall_dist_sigma": _dist_sigma(cw),   # how many σ above spot
            "put_wall_dist_sigma": _dist_sigma(pw),    # how many σ below spot
            "sigma": sig,
            "upper_1sigma": upper,
            "lower_1sigma": lower,
            "regime": plan["regime"],
            "bias": plan["bias"],
            "playbook": plan["playbook"],
            "agg": agg,                            # for the chart
        }
    except Exception as exc:
        return {"symbol": fut_sym, "error": str(exc)}


def compute_all(now: _datetime | None = None) -> dict:
    """Day-trade GEX for ES, NQ, GC."""
    return {s: compute_symbol(s, now=now) for s in _FUTURES_CBOE}


def pipe_string(results: dict) -> str:
    """SYMBOL:PRICE:LABEL pipe string for TradingView / algos."""
    order = [
        ("call_wall", "Call Wall"),
        ("secondary_call_wall", "Call Wall 2"),
        ("upper_1sigma", "Upper 1σ"),
        ("gamma_flip", "Zero Gamma"),
        ("lower_1sigma", "Lower 1σ"),
        ("put_wall", "Put Wall"),
        ("secondary_put_wall", "Put Wall 2"),
    ]
    parts = []
    for sym in ("ES", "NQ", "GC"):
        d = results.get(sym, {})
        if d.get("error"):
            continue
        for key, label in order:
            v = d.get(key)
            if v is not None:
                parts.append(f"{sym}:{v:.0f}:{label}")
    return "|".join(parts)


# ── Snapshot logger (foundation for backtesting which levels actually pay) ─────
def snapshot_levels(results: dict, path: str = "gex_snapshots.csv") -> str:
    """
    Append the current computed levels for every symbol to a CSV, timestamped.

    This is the missing feedback loop: log levels through the session, then later
    score each one (did spot touch-and-reject a wall? did a flip-cross precede a
    trend day?) to learn the *hit-rate per level type per regime*. Correct GEX
    math is necessary but not sufficient — only measured hit-rates tell you which
    signals to trade. Call this on each refresh to build the dataset.
    """
    import csv, os
    ts = (_datetime.now(_ET) if _ET else _datetime.now()).isoformat()
    fields = ["timestamp", "symbol", "spot", "gamma_flip", "call_wall", "put_wall",
              "secondary_call_wall", "secondary_put_wall", "net_gex", "net_vex",
              "net_cex", "charm_drift", "call_wall_strength", "put_wall_strength",
              "upper_1sigma", "lower_1sigma", "regime", "bias", "expiry", "dte"]
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for sym, d in results.items():
            if not isinstance(d, dict) or d.get("error"):
                continue
            row = {k: d.get(k) for k in fields}
            row["timestamp"], row["symbol"] = ts, sym
            w.writerow(row)
    return path
