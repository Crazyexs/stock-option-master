"""
earnings_core.py — earnings-date awareness for the single-name option tools.
================================================================================
Pure-logic module (no Streamlit side effects). The Trade Finder / scanner rank
option trades but are blind to the one scheduled event that dominates a single
stock's vol: EARNINGS. Buying premium into a print means paying peak IV and
eating the post-report IV crush; selling naked premium into it is uncapped event
risk. This module surfaces the next earnings date and flags whether it lands
inside a given DTE window.

DATA
----
yfinance (no key). Earnings dates come from `Ticker.get_earnings_dates()` with a
fallback to `Ticker.calendar`. Best-effort: returns None / [] on any failure, so
a missing date never breaks the caller.

HONEST LIMITATIONS
------------------
* Earnings dates are estimates until a company confirms; "AMC/BMO" (after-market
  / before-market) timing can shift. Treat the date as ±1 session.
* yfinance can rate-limit on bursts; the watchlist helper fails soft per ticker.
"""

from __future__ import annotations

from datetime import date as _date, datetime as _datetime, timezone


def _to_date(v) -> _date | None:
    try:
        if v is None:
            return None
        if isinstance(v, _datetime):
            return v.date()
        if isinstance(v, _date):
            return v
        ts = __import__("pandas").Timestamp(v)
        return ts.date()
    except Exception:
        return None


def next_earnings(ticker: str, today: _date | None = None) -> dict | None:
    """
    Next (or most recent upcoming) earnings date for one ticker:
        {ticker, date, days_away, is_upcoming}
    Returns None if no date can be found.
    """
    today = today or _date.today()
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
    except Exception:
        return None

    candidates: list[_date] = []
    # Primary: the earnings-dates table (past + future).
    try:
        ed = tk.get_earnings_dates(limit=12)
        if ed is not None and not ed.empty:
            for idx in ed.index:
                d = _to_date(idx)
                if d:
                    candidates.append(d)
    except Exception:
        pass
    # Fallback: the calendar dict.
    if not candidates:
        try:
            cal = tk.calendar
            raw = None
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date")
            elif cal is not None and hasattr(cal, "loc"):
                raw = cal.loc["Earnings Date"].tolist()
            for v in (raw if isinstance(raw, (list, tuple)) else [raw]):
                d = _to_date(v)
                if d:
                    candidates.append(d)
        except Exception:
            pass

    if not candidates:
        return None
    future = sorted(d for d in candidates if d >= today)
    chosen = future[0] if future else sorted(candidates)[-1]
    return {"ticker": ticker.upper(), "date": chosen.isoformat(),
            "days_away": (chosen - today).days,
            "is_upcoming": chosen >= today}


def earnings_in_window(ticker: str, dte_min: int, dte_max: int,
                       today: _date | None = None) -> dict:
    """
    Does the next earnings land inside [today+dte_min, today+dte_max]? This is the
    IV-crush gate for an option trade with that DTE window.
    Returns {ticker, date, days_away, in_window, warning}.
    """
    info = next_earnings(ticker, today=today)
    if not info or not info["is_upcoming"]:
        return {"ticker": ticker.upper(), "date": info["date"] if info else None,
                "days_away": info["days_away"] if info else None,
                "in_window": False, "warning": None}
    da = info["days_away"]
    in_win = dte_min <= da <= dte_max
    warning = None
    if in_win:
        warning = (f"Earnings on {info['date']} ({da}d) fall INSIDE the {dte_min}-{dte_max} "
                   "DTE window — long premium pays peak IV then crushes; short premium "
                   "carries uncapped event risk. Size for it or trade a different expiry.")
    elif da <= dte_max:
        warning = f"Earnings on {info['date']} ({da}d) are near the window — watch IV."
    return {"ticker": ticker.upper(), "date": info["date"], "days_away": da,
            "in_window": in_win, "warning": warning}


def watchlist_calendar(tickers: list[str], today: _date | None = None,
                       horizon_days: int = 45) -> list[dict]:
    """Upcoming earnings for a list of tickers within `horizon_days`, soonest first."""
    today = today or _date.today()
    out = []
    for t in tickers:
        info = next_earnings(t, today=today)
        if info and info["is_upcoming"] and info["days_away"] <= horizon_days:
            out.append(info)
    out.sort(key=lambda x: x["days_away"])
    return out
