"""
macro_core.py — NQ directional-probability model (cross-asset + macro + GEX).
================================================================================
Pure-logic module (no Streamlit side effects). It answers one question:

    "Given everything moving right now — other assets, their correlations to NQ,
     the US event calendar, and NQ's own dealer-gamma regime — which way is NQ
     tilted, how confident should I be, and how do I trade it?"

THE METHOD (and why it's built this way)
----------------------------------------
1.  CROSS-ASSET TILT — data-driven, not hardcoded signs.
        contribution_i = corr(asset_i, NQ) × z(asset_i's latest move)
    `corr` is measured over a rolling window, so it already carries the *sign*
    and *strength* of each relationship and adapts when regimes flip (e.g. the
    stock/bond correlation has flipped sign several times in history). `z` is the
    asset's latest return standardised by its own vol, so a 2% VIX pop and a 0.3%
    ES move are comparable. Sum the contributions → a single NQ tilt; a damped
    logistic maps it to an implied P(up).

2.  GEX REGIME (from gex_core) decides HOW the tilt plays out, not the direction:
        • POSITIVE gamma (pin) → market mean-reverts → tilt has weak intraday
          follow-through; use it to pick WHICH wall to lean on, fade the other.
        • NEGATIVE gamma (trend) → market trends → tilt is far more likely to
          carry; trade in the tilt's direction on breaks.

3.  EVENT RISK — FOMC / NFP expand vol and BREAK gamma pins. Near an event the
    model dampens confidence and explicitly says "don't fade walls / size down".

HONEST LIMITATIONS
------------------
* P(up) is a *heuristic composite*, NOT a calibrated probability. It is clamped
  to [15%, 85%] to avoid false confidence, and it must be validated against the
  snapshot log before it's sized against real money.
* Cross-asset moves use daily (close-to-close) data — coarse for intraday timing.
  Treat the tilt as the day's lean, confirmed by price action at GEX levels.
* The FOMC/NFP calendar below is static — VERIFY FOMC dates against the Fed site
  and add CPI from the BLS schedule; only NFP (first Friday) is auto-computed.
"""

from __future__ import annotations

import math
from datetime import date as _date, datetime as _datetime, timedelta

import numpy as np
import pandas as pd

import gex_core as gx


# ── Universe: cross-asset drivers of NQ ───────────────────────────────────────
# name -> (yfinance ticker, human label, expected sign vs NQ for reference only)
ASSETS = {
    "NQ":    ("NQ=F",      "Nasdaq-100 future (target)",      +1),
    "ES":    ("ES=F",      "S&P 500 future",                  +1),
    "VIX":   ("^VIX",      "Volatility index",                -1),
    "DXY":   ("DX-Y.NYB",  "US Dollar index",                 -1),
    "US10Y": ("^TNX",      "US 10-year yield",                -1),
    "GOLD":  ("GC=F",      "Gold future",                      0),
    "OIL":   ("CL=F",      "WTI crude",                        0),
    "BTC":   ("BTC-USD",   "Bitcoin (24/7 risk proxy)",       +1),
    "SEMI":  ("SOXX",      "Semiconductors (NQ leader)",      +1),
}

# ── US macro calendar ─────────────────────────────────────────────────────────
# FOMC 2026 *decision* days (second day of each meeting). VERIFY vs federalreserve.gov.
FOMC_2026 = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
             "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]


def _parse(d: str) -> _date:
    return _datetime.strptime(d, "%Y-%m-%d").date()


def _first_friday(y: int, m: int) -> _date:
    d = _date(y, m, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7)   # Mon=0 … Fri=4


def next_events(today: _date | None = None, n: int = 5) -> list[tuple]:
    """Upcoming high-impact US events: FOMC decisions + NFP (first Friday)."""
    today = today or _date.today()
    evs: list[tuple] = []
    for d in FOMC_2026:
        dd = _parse(d)
        if dd >= today:
            evs.append((dd, "FOMC rate decision", "high"))
    for off in range(0, 3):                       # this month + next two
        y, m = today.year, today.month + off
        while m > 12:
            m -= 12
            y += 1
        ff = _first_friday(y, m)
        if ff >= today:
            evs.append((ff, "Nonfarm Payrolls (NFP)", "high"))
    evs.sort(key=lambda x: x[0])
    return evs[:n]


def event_risk(today: _date | None = None) -> dict:
    """Proximity of the next high-impact event, with a tradeability flag."""
    today = today or _date.today()
    ev = next_events(today, 6)
    if not ev:
        return {"flag": "clear", "days": None, "event": None, "upcoming": []}
    nxt = ev[0]
    days = (nxt[0] - today).days
    if days <= 0:
        flag = "TODAY"
    elif days == 1:
        flag = "tomorrow"
    elif days <= 3:
        flag = "this week"
    else:
        flag = "clear"
    return {"flag": flag, "days": days, "event": nxt[1], "date": nxt[0].isoformat(),
            "upcoming": [(d.isoformat(), name) for d, name, _ in ev]}


# ── Cross-asset data ──────────────────────────────────────────────────────────
def download_closes(period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """Daily close matrix for the whole universe (robust to per-ticker failures)."""
    try:
        import yfinance as yf
    except Exception:
        return pd.DataFrame()
    tickers = [v[0] for v in ASSETS.values()]
    closes: dict[str, pd.Series] = {}
    try:
        raw = yf.download(tickers, period=period, interval=interval,
                          progress=False, group_by="ticker", threads=True)
    except Exception:
        raw = None
    for name, (tk, _lbl, _sgn) in ASSETS.items():
        s = None
        if raw is not None:
            try:
                s = raw[tk]["Close"].dropna()
            except Exception:
                s = None
        if s is None or s.empty:                  # per-ticker fallback
            try:
                s = yf.Ticker(tk).history(period=period, interval=interval)["Close"].dropna()
            except Exception:
                s = None
        if s is not None and not s.empty:
            closes[name] = s
    if not closes:
        return pd.DataFrame()
    return pd.DataFrame(closes).sort_index()


def returns_frame(closes: pd.DataFrame) -> pd.DataFrame:
    if closes is None or closes.empty:
        return pd.DataFrame()
    return closes.pct_change().dropna(how="all")


def correlation_matrix(returns: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    if returns is None or returns.empty:
        return pd.DataFrame()
    return returns.tail(window).corr().round(2)


def cross_asset_tilt(returns: pd.DataFrame, window: int = 60) -> dict:
    """
    contribution_i = corr(asset_i, NQ) × z(latest move of asset_i)
    tilt = mean contribution; P(up) = clamped logistic of the tilt.
    """
    if returns is None or returns.empty or "NQ" not in returns.columns:
        return {}
    r = returns.tail(window)
    nq = r["NQ"].dropna()
    if len(nq) < 20:
        return {}
    comps = []
    for col in r.columns:
        if col == "NQ":
            continue
        s = r[col].dropna()
        common = s.index.intersection(nq.index)
        if len(common) < 20:
            continue
        s_common = s.loc[common]
        corr = float(np.corrcoef(s_common, nq.loc[common])[0, 1])
        if math.isnan(corr):
            continue
        sd = float(s_common.std())
        # "Latest move" must be CONTEMPORANEOUS with NQ — assets trade on
        # different calendars (BTC weekends; a stale ticker's last bar can be
        # days old), so use the most recent bar this asset SHARES with NQ, not
        # its own iloc[-1] (which would mix non-aligned days into the tilt).
        last = float(s_common.iloc[-1])
        z = max(-3.0, min(3.0, last / sd)) if sd > 0 else 0.0
        comps.append({"asset": col, "corr": round(corr, 2),
                      "move_pct": round(last * 100, 2), "z_move": round(z, 2),
                      "contribution": round(corr * z, 3)})
    if not comps:
        return {}
    tilt = float(np.mean([c["contribution"] for c in comps]))
    p_up = 1.0 / (1.0 + math.exp(-1.6 * tilt))
    p_up = max(0.15, min(0.85, p_up))
    nq_move = round(float(nq.iloc[-1]) * 100, 2)
    return {"components": sorted(comps, key=lambda c: abs(c["contribution"]), reverse=True),
            "tilt": round(tilt, 3), "p_up": round(p_up * 100, 1), "nq_move_pct": nq_move}


# ── Top-level: combine cross-asset tilt + GEX regime + event risk ─────────────
def compute_nq_bias(period: str = "6mo") -> dict:
    """Full NQ directional read. Returns a flat dict for the UI."""
    out: dict = {"errors": []}
    closes = download_closes(period=period)
    returns = returns_frame(closes)
    if returns.empty:
        out["errors"].append("No cross-asset data (yfinance).")
    tilt = cross_asset_tilt(returns)
    corr = correlation_matrix(returns)
    ev = event_risk()
    news = news_pulse()
    try:
        gex = gx.compute_symbol("NQ")
    except Exception as exc:
        gex = {"error": str(exc)}

    p_up = tilt.get("p_up", 50.0)

    # GEX regime: how the tilt plays out (pin vs trend).
    regime_pos, regime_txt = None, "n/a"
    if isinstance(gex, dict) and not gex.get("error"):
        flip, spot, net = gex.get("gamma_flip"), gex.get("spot"), gex.get("net_gex", 0.0)
        regime_pos = (spot >= flip) if (flip is not None and spot is not None) else (net >= 0)
        regime_txt = ("POSITIVE γ (pin / mean-revert)" if regime_pos
                      else "NEGATIVE γ (trend / momentum)")

    # Confidence from how far P(up) is from a coin flip, damped near events and
    # in pin regime (where directional follow-through is weak intraday).
    raw_conf = abs(p_up - 50.0) / 35.0            # 0 … ~1
    if ev["flag"] in ("TODAY", "tomorrow"):
        raw_conf *= 0.4
    elif ev["flag"] == "this week":
        raw_conf *= 0.7
    if regime_pos is True:
        raw_conf *= 0.7                            # pin caps directional moves
    elif regime_pos is False:
        raw_conf *= 1.15                           # trend lets the tilt run
    # A hot tape (fresh HIGH-impact headlines) breaks gamma pins the same way a
    # scheduled event does — a daily cross-asset tilt can't see a breaking shock,
    # so damp directional confidence until price confirms the new regime.
    if news.get("level") == "elevated":
        raw_conf *= 0.5
    elif news.get("level") == "watch":
        raw_conf *= 0.8
    conf_pct = max(0.0, min(1.0, raw_conf)) * 100.0
    confidence = "high" if conf_pct >= 66 else ("medium" if conf_pct >= 33 else "low")

    if p_up >= 58:
        bias = "UP"
    elif p_up <= 42:
        bias = "DOWN"
    else:
        bias = "NEUTRAL"

    play = _build_play(bias, regime_pos, ev, gex)
    if news.get("level") == "elevated":
        cats = ", ".join(news.get("categories", [])) or "breaking headlines"
        play.insert(0, f" Tape HOT ({news.get('n_recent_high',0)} HIGH-impact items, "
                       f"lean {news.get('tone','mixed')} — {cats}): a breaking shock can "
                       "break gamma pins — confidence is damped; wait for price to confirm "
                       "the new regime before sizing.")

    out.update({
        "p_up": p_up,
        "bias": bias,
        "confidence": confidence,
        "confidence_pct": round(conf_pct, 0),
        "tilt": tilt.get("tilt"),
        "components": tilt.get("components", []),
        "nq_move_pct": tilt.get("nq_move_pct"),
        "corr": corr,
        "regime": regime_txt,
        "regime_pos": regime_pos,
        "event": ev,
        "news": news,
        "gex": gex,
        "play": play,
    })
    return out


def news_pulse() -> dict:
    """
    Best-effort read of the live tape's market-impact heat, used to damp the NQ
    directional confidence on breaking shocks. Never raises; if news_core or the
    feeds are unavailable, returns a calm/neutral pulse so the model is unaffected.
    """
    try:
        import news_core as nc
        # FinancialJuice only — fast + reliable. The model must not block on the
        # flaky Nitter retry loop (that feed lives on the Macro News page).
        items = nc.get_headlines(sources=["FinancialJuice"], min_impact="LOW", limit=60)
        return nc.market_impact_summary(items)
    except Exception:
        return {"level": "calm", "n_high": 0, "n_recent_high": 0,
                "tone": "mixed", "categories": []}


def _build_play(bias: str, regime_pos, ev: dict, gex: dict) -> list[str]:
    pb: list[str] = []
    cw = gex.get("call_wall") if isinstance(gex, dict) else None
    pw = gex.get("put_wall") if isinstance(gex, dict) else None
    flip = gex.get("gamma_flip") if isinstance(gex, dict) else None

    if ev["flag"] in ("TODAY", "tomorrow"):
        pb.append(f" {ev['event']} {ev['flag']} — vol expands and gamma pins BREAK. "
                  "Don't fade walls; size down or wait for the post-event regime to set.")

    if regime_pos is True:   # pin
        pb.append("Pin regime: NQ mean-reverts intraday — the cross-asset tilt picks "
                  "which wall to lean on, it does NOT mean chase.")
        if bias == "UP" and pw:
            pb.append(f"Favour LONG: buy dips into put wall {pw:,.0f}, target flip "
                      f"{flip:,.0f}" + (f" then call wall {cw:,.0f}." if cw else "."))
        elif bias == "DOWN" and cw:
            pb.append(f"Favour SHORT: sell rallies into call wall {cw:,.0f}, target flip "
                      f"{flip:,.0f}" + (f" then put wall {pw:,.0f}." if pw else "."))
        else:
            pb.append("Tilt neutral: fade BOTH walls back toward the flip (range day).")
    elif regime_pos is False:  # trend
        pb.append("Trend regime: the tilt is more likely to carry — trade WITH it on breaks.")
        if bias == "UP" and cw:
            pb.append(f"Favour LONG breakouts; reclaim of flip {flip:,.0f} → squeeze toward "
                      f"call wall {cw:,.0f}.")
        elif bias == "DOWN" and pw:
            pb.append(f"Favour SHORT breakdowns; loss of put wall {pw:,.0f} accelerates lower.")
        else:
            pb.append("Tilt neutral in a trend regime: wait for a clean break of flip or a wall.")
    else:
        pb.append("No GEX regime available — trade the cross-asset tilt only with reduced size.")

    pb.append("Confirm with price action at the level — these are probabilities, not signals.")
    return pb


def snapshot_nq_bias(res: dict, path: str = "nq_bias_snapshots.csv") -> str:
    """Append the current NQ bias read for later hit-rate scoring (backtest feed)."""
    import csv, os
    ts = _datetime.now().isoformat()
    gex = res.get("gex", {}) if isinstance(res.get("gex"), dict) else {}
    row = {
        "timestamp": ts,
        "p_up": res.get("p_up"),
        "bias": res.get("bias"),
        "confidence": res.get("confidence"),
        "tilt": res.get("tilt"),
        "regime": res.get("regime"),
        "nq_move_pct": res.get("nq_move_pct"),
        "event_flag": res.get("event", {}).get("flag"),
        "next_event": res.get("event", {}).get("event"),
        "spot": gex.get("spot"),
        "gamma_flip": gex.get("gamma_flip"),
        "call_wall": gex.get("call_wall"),
        "put_wall": gex.get("put_wall"),
    }
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new_file:
            w.writeheader()
        w.writerow(row)
    return path
