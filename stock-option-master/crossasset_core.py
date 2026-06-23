"""
crossasset_core.py — cross-asset macro dashboard (context for the NQ/ES tape).
================================================================================
Pure-logic module (no Streamlit side effects). macro_core uses a cross-asset
universe to build the NQ tilt; this module is the *standalone dashboard* version:
it pulls the levels and daily moves for the things that push index futures around
— rates, the dollar, oil, crypto, and the equity sectors — plus a couple of free
breadth proxies, and packages them for display.

WHAT'S HERE AND WHY IT MATTERS FOR OPTIONS/GEX
----------------------------------------------
* RATES — the 2Y and 10Y yields and the 2s10s curve. Tech (NQ) is long-duration:
  a sharp 10Y rise compresses multiples and pressures the Nasdaq. An inverted /
  steepening curve is the recession-timing signal everyone watches.
* DOLLAR (DXY) — risk-off and tightening proxy; a strong dollar is a headwind for
  risk assets and commodities.
* COMMODITIES — WTI crude (inflation/geopolitics) and gold (real-rate / haven).
* CRYPTO (BTC) — the 24/7 risk barometer; often leads risk sentiment into the
  cash open.
* SECTORS — the S&P sector ETFs ranked by the day's move tell you whether the
  tape is risk-on (tech/discretionary leading) or defensive (staples/utilities
  bid). Sector breadth confirms or diverges from the GEX-implied bias.

DATA
----
yfinance only (no key). Best-effort: each quote is independent, so one failed
ticker never blanks the board.

HONEST LIMITATIONS
------------------
* Daily close-to-close moves — coarse for intraday timing; treat as context.
* ^TNX / ^FVX are yields ×10 on Yahoo (e.g. 43.2 = 4.32%); handled here.
* Breadth proxies are ETF/relative measures, not true exchange A/D line internals.
"""

from __future__ import annotations

# Yahoo yield tickers are quoted ×10 (4.32% shows as 43.2). Yahoo has no clean
# 2Y ticker, so the front is the 13-week bill (^IRX); we report the 3m10y spread,
# a Fed-preferred recession gauge, rather than mislabel anything "2Y".
_YIELDS = {
    "US 3M":  "^IRX",
    "US 5Y":  "^FVX",
    "US 10Y": "^TNX",
    "US 30Y": "^TYX",
}

_MACRO = {
    "ES (S&P)":   "ES=F",
    "NQ (Nasdaq)": "NQ=F",
    "RTY (Rus2k)": "RTY=F",
    "DXY (Dollar)": "DX-Y.NYB",
    "WTI Crude":  "CL=F",
    "Gold":       "GC=F",
    "Bitcoin":    "BTC-USD",
    "VIX":        "^VIX",
}

# SPDR sector ETFs — the risk-on/off read.
_SECTORS = {
    "Tech (XLK)":        "XLK",
    "Discretionary (XLY)": "XLY",
    "Communications (XLC)": "XLC",
    "Financials (XLF)":  "XLF",
    "Energy (XLE)":      "XLE",
    "Industrials (XLI)": "XLI",
    "Health (XLV)":      "XLV",
    "Staples (XLP)":     "XLP",
    "Utilities (XLU)":   "XLU",
    "Materials (XLB)":   "XLB",
    "Real Estate (XLRE)": "XLRE",
}


def _quote_block(tickers: dict[str, str], is_yield: bool = False) -> list[dict]:
    """(label, last, prev, pct) for a dict of tickers — robust to failures."""
    try:
        import yf_session as yfs
        syms = list(dict.fromkeys(tickers.values()))
        data = yfs.download(syms, period="5d", interval="1d",
                            progress=False, group_by="ticker", threads=False)
    except Exception:
        return []
    out = []
    for label, tk in tickers.items():
        try:
            col = data[tk]["Close"].dropna() if len(syms) > 1 else data["Close"].dropna()
            if col.empty:
                continue
            last = float(col.iloc[-1])
            prev = float(col.iloc[-2]) if len(col) > 1 else last
            if is_yield:
                # Yahoo quotes these yield indices ×10 on some symbols/versions
                # (43.2 = 4.32%) and raw on others. No real Treasury yield is
                # >20%, so auto-detect the scale instead of hardcoding ÷10.
                if last > 20:
                    last, prev = last / 10.0, prev / 10.0
                chg = round(last - prev, 3)   # yields: report change in pp
                out.append({"label": label, "last": round(last, 3),
                            "prev": round(prev, 3), "chg": chg, "is_yield": True})
            else:
                pct = (last / prev - 1.0) * 100.0 if prev else 0.0
                out.append({"label": label, "last": last, "prev": prev,
                            "pct": round(pct, 2), "is_yield": False})
        except Exception:
            continue
    return out


def get_yields() -> dict:
    """Treasury yields + the 3m10y curve slope (in basis points)."""
    rows = _quote_block(_YIELDS, is_yield=True)
    by = {r["label"]: r for r in rows}
    slope = None
    if "US 3M" in by and "US 10Y" in by:
        slope = round((by["US 10Y"]["last"] - by["US 3M"]["last"]) * 100.0, 1)  # bp
    curve = ("inverted (recession signal)" if (slope is not None and slope < 0)
             else "normal / steepening" if slope is not None else "n/a")
    return {"rows": rows, "slope_3m10y_bp": slope, "curve": curve}


def get_macro() -> list[dict]:
    """Index futures, dollar, commodities, crypto, VIX — daily moves."""
    return _quote_block(_MACRO)


def get_sectors() -> dict:
    """Sector ETF moves, ranked, with a risk-on/off read from the leaders."""
    rows = sorted(_quote_block(_SECTORS), key=lambda r: r.get("pct", 0), reverse=True)
    if not rows:
        return {"rows": [], "tone": "n/a", "read": "Sector data unavailable."}
    leaders = {r["label"] for r in rows[:3]}
    defensive = {"Staples (XLP)", "Utilities (XLU)", "Health (XLV)", "Real Estate (XLRE)"}
    cyclical = {"Tech (XLK)", "Discretionary (XLY)", "Communications (XLC)",
                "Financials (XLF)", "Industrials (XLI)", "Energy (XLE)"}
    n_def = len(leaders & defensive)
    n_cyc = len(leaders & cyclical)
    if n_cyc > n_def:
        tone, read = "risk-on", "Cyclicals leading — tape is risk-on; tilt supports longs."
    elif n_def > n_cyc:
        tone, read = "risk-off", "Defensives leading — tape is risk-off; caution on longs."
    else:
        tone, read = "mixed", "No clear sector leadership — mixed/rotational tape."
    return {"rows": rows, "tone": tone, "read": read}


def dashboard() -> dict:
    """One-call snapshot for the page."""
    return {"yields": get_yields(), "macro": get_macro(), "sectors": get_sectors()}
