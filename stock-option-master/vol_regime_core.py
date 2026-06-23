"""
vol_regime_core.py — VIX term structure & volatility regime.
================================================================================
Pure-logic module (no Streamlit side effects). The GEX engine reads IV off the
option chain, but it never looks at the VIX COMPLEX — and the *shape* of that
complex is one of the cleanest regime filters there is.

THE TERM STRUCTURE
------------------
    VIX9D  — 9-day implied vol  (front, most jumpy)
    VIX    — 30-day implied vol  (the headline number)
    VIX3M  — 3-month implied vol  (back)
    VVIX   — vol-of-vol (the implied vol of VIX itself; tail-hedging demand)

In a calm, risk-on tape the curve is in CONTANGO (VIX9D < VIX < VIX3M): near-term
vol is cheaper than far-term, dealers are paid to be short vol, and equity drift
is positive. When fear hits, the front spikes and the curve INVERTS into
BACKWARDATION (VIX9D > VIX3M): the market is paying up for immediate protection.

WHY IT PAIRS WITH GEX
---------------------
The two signals answer different questions and confirm each other:
    • GEX sign  → are dealers long gamma (pin) or short gamma (amplify)?
    • Term ratio → is the vol market calm (contango) or stressed (backwardation)?
Backwardation + negative GEX is the high-conviction trend/expansion regime — both
say "moves get amplified". Contango + positive GEX is the classic grind/pin. When
they disagree, conviction is lower and you wait.

KEY RATIOS
----------
    VIX9D / VIX     < 1 = front calm (contango at the short end); > 1 = front bid
    VIX / VIX3M     < 1 = healthy contango;  > 1 = backwardation (stress)
    VVIX            > ~110 = elevated tail-hedging demand; < ~90 = complacent

DATA
----
CBOE free CDN (the same delayed-quote host gex_core already uses) — no API key.
Falls back to yfinance (^VIX, ^VIX9D, ^VIX3M, ^VVIX) if the CDN is unreachable.

HONEST LIMITATIONS
------------------
* Delayed (~15 min) — fine for a regime read, not for scalping the VIX itself.
* Thresholds (1.0 cross, VVIX 90/110) are conventional desk heuristics, not laws;
  the regime is a prior to confirm against price at your GEX levels.
"""

from __future__ import annotations

import requests

# CBOE delayed index quotes (underscore prefix = index product).
_CBOE_QUOTE = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{sym}.json"
_MEMBERS = ("VIX9D", "VIX", "VIX3M", "VVIX")
_YF = {"VIX9D": "^VIX9D", "VIX": "^VIX", "VIX3M": "^VIX3M", "VVIX": "^VVIX"}


def _fetch_cboe_quote(sym: str) -> float | None:
    try:
        r = requests.get(_CBOE_QUOTE.format(sym=sym),
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        data = r.json().get("data", {})
        v = data.get("current_price") or data.get("last") or data.get("close")
        return float(v) if v else None
    except Exception:
        return None


def _fetch_yf_quote(sym: str) -> float | None:
    try:
        import yf_session as yfs
        h = yfs.make_ticker(_YF[sym]).history(period="2d")
        return float(h["Close"].iloc[-1]) if not h.empty else None
    except Exception:
        return None


def fetch_vix_complex() -> dict:
    """{VIX9D, VIX, VIX3M, VVIX} latest levels. CBOE first, yfinance fallback."""
    out = {}
    for sym in _MEMBERS:
        out[sym] = _fetch_cboe_quote(sym) or _fetch_yf_quote(sym)
    return out


def _ratio(a, b):
    return round(a / b, 3) if (a and b) else None


def classify_regime(levels: dict) -> dict:
    """Turn the four levels into ratios + a named regime + a one-line read."""
    vix9d, vix, vix3m, vvix = (levels.get(k) for k in _MEMBERS)
    front = _ratio(vix9d, vix)          # short-end slope
    main = _ratio(vix, vix3m)           # headline term-structure slope

    if main is None:
        return {"levels": levels, "front_ratio": front, "term_ratio": main,
                "structure": "n/a", "regime": "n/a",
                "read": "VIX term structure unavailable (feed down)."}

    if main > 1.05:
        structure = "deep backwardation"
    elif main > 1.0:
        structure = "backwardation"
    elif main > 0.95:
        structure = "flat / transition"
    else:
        structure = "contango"

    backwardation = main >= 1.0
    if backwardation:
        regime = "STRESS — vol expansion / trend"
        read = ("Front-month vol bid over 3-month (curve inverted): the market is "
                "paying up for immediate protection. Expect expansion and trend; "
                "pairs with NEGATIVE GEX for high-conviction momentum. Do not fade.")
    elif main < 0.90:
        regime = "CALM — grind / mean-revert"
        read = ("Healthy contango: near-term vol cheap vs back. Risk-on drift, "
                "vol sellers in control; pairs with POSITIVE GEX for the pin/fade "
                "playbook.")
    else:
        regime = "TRANSITION — mixed"
        read = ("Curve near flat — regime is turning. Lower conviction; let price "
                "at your GEX levels break the tie.")

    vvix_note = None
    if vvix is not None:
        if vvix >= 110:
            vvix_note = f"VVIX {vvix:.0f} — elevated tail-hedging demand (vol-of-vol bid)."
        elif vvix <= 90:
            vvix_note = f"VVIX {vvix:.0f} — complacent (cheap vol-of-vol)."
        else:
            vvix_note = f"VVIX {vvix:.0f} — neutral."

    return {
        "levels": levels,
        "front_ratio": front,      # VIX9D / VIX
        "term_ratio": main,        # VIX / VIX3M  (>1 = backwardation)
        "structure": structure,
        "backwardation": backwardation,
        "regime": regime,
        "read": read,
        "vvix_note": vvix_note,
    }


def get_regime() -> dict:
    """One-call: fetch the complex and classify it."""
    return classify_regime(fetch_vix_complex())
