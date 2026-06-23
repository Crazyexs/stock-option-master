"""
econ_calendar_core.py — LIVE US economic calendar (replaces the static list).
================================================================================
Pure-logic module (no Streamlit side effects). macro_core shipped a hardcoded
FOMC-2026 list with a "VERIFY THESE DATES MANUALLY" warning and no CPI/PCE/PPI —
a maintenance landmine that silently rots. This pulls the real calendar live.

SOURCE
------
Nasdaq's public economic-events API (no key, just a browser User-Agent):
    https://api.nasdaq.com/api/calendar/economicevents?date=YYYY-MM-DD
Each row: time (GMT), country, eventName, actual, consensus, previous. We query
a date range day-by-day, keep the US events, and tag each with a HIGH/MEDIUM/LOW
market-impact prior from the event name (the Nasdaq feed has no impact field).

IMPACT MODEL
------------
Same philosophy as news_core: a transparent keyword prior, not an LLM. The big
vol drivers — FOMC, CPI, PCE, NFP/payrolls, GDP, ISM, retail sales — are HIGH;
second-tier data (claims, confidence, housing) MEDIUM; everything else LOW. Tune
the maps; thresholds are deliberately conservative.

HONEST LIMITATIONS
------------------
* Best-effort and never raises: if Nasdaq is unreachable the caller falls back to
  the static FOMC/NFP computation in macro_core, so the app degrades gracefully.
* Impact is a heuristic prior on the *scheduled* event, not the realised surprise
  (which depends on actual vs consensus). Confirm against price at your levels.
* Times are GMT as published; convert in the UI if you need ET.
"""

from __future__ import annotations

import re
import time
from datetime import date as _date, datetime as _datetime, timedelta

import requests

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "application/json"}
_URL = "https://api.nasdaq.com/api/calendar/economicevents?date={d}"
_TIMEOUT = 10

# ── Impact keyword model (event name -> HIGH / MEDIUM) ────────────────────────
_HIGH = (
    r"\bfomc\b", r"\bfed(?:eral)? funds\b", r"\binterest rate decision\b",
    r"\bcpi\b", r"\bcore cpi\b", r"\bpce\b", r"\bppi\b", r"\binflation\b",
    r"\bnonfarm\b", r"\bnon-farm\b", r"\bpayroll", r"\bunemployment rate\b",
    r"\bgdp\b", r"\bism manufacturing pmi\b", r"\bism (?:services|non-manufacturing) pmi\b",
    r"\bretail sales\b", r"\bpowell\b", r"\bfed chair\b",
)
_MEDIUM = (
    r"\bjobless claims\b", r"\binitial claims\b", r"\badp\b", r"\bjolts\b",
    r"\bconsumer confidence\b", r"\bconsumer sentiment\b", r"\bdurable goods\b",
    r"\bpmi\b", r"\bphiladelphia fed\b", r"\bempire state\b", r"\bhousing starts\b",
    r"\bbuilding permits\b", r"\bnew home sales\b", r"\bexisting home sales\b",
    r"\btrade balance\b", r"\bfactory orders\b", r"\bindustrial production\b",
    r"\bchallenger\b", r"\bbeige book\b", r"\bfed (?:speak|speaks|official|governor)\b",
)


def classify_event(name: str) -> str:
    n = (name or "").lower()
    for p in _HIGH:
        if re.search(p, n):
            return "HIGH"
    for p in _MEDIUM:
        if re.search(p, n):
            return "MEDIUM"
    return "LOW"


# ── Fetch (per-day, with a small in-process cache) ────────────────────────────
_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 1800.0     # 30 min — the schedule barely changes intraday


def _fetch_day(d: str) -> list[dict]:
    now = time.time()
    hit = _CACHE.get(d)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    try:
        r = requests.get(_URL.format(d=d), headers=_UA, timeout=_TIMEOUT)
        r.raise_for_status()
        rows = (r.json().get("data") or {}).get("rows") or []
    except Exception:
        rows = []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("eventName")
        if not name or name == "Event":            # skip the header echo row
            continue
        out.append({
            "date": d,
            "time": (row.get("gmt") or "").strip(),
            "country": (row.get("country") or "").strip(),
            "event": name.strip(),
            "actual": (row.get("actual") or "").strip(),
            "consensus": (row.get("consensus") or "").strip(),
            "previous": (row.get("previous") or "").strip(),
            "impact": classify_event(name),
        })
    _CACHE[d] = (now, out)
    return out


def upcoming_us_events(days: int = 10, today: _date | None = None,
                       min_impact: str = "MEDIUM",
                       us_only: bool = True) -> list[dict]:
    """
    US (or global) economic events over the next `days`, filtered to >= min_impact
    and sorted by date/time. Best-effort: returns [] if the feed is unreachable.
    """
    today = today or _date.today()
    floor = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}.get(min_impact.upper(), 1)
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    out: list[dict] = []
    for off in range(days + 1):
        d = (today + timedelta(days=off)).isoformat()
        for ev in _fetch_day(d):
            if us_only and ev["country"] != "United States":
                continue
            if rank[ev["impact"]] < floor:
                continue
            out.append(ev)
    out.sort(key=lambda e: (e["date"], e["time"] or "99:99"))
    return out


def next_high_impact(today: _date | None = None, days: int = 14) -> dict | None:
    """The soonest HIGH-impact US event as {date, event, days_away}, or None."""
    today = today or _date.today()
    evs = upcoming_us_events(days=days, today=today, min_impact="HIGH")
    if not evs:
        return None
    nxt = evs[0]
    d0 = _datetime.strptime(nxt["date"], "%Y-%m-%d").date()
    return {"date": nxt["date"], "event": nxt["event"],
            "days_away": (d0 - today).days, "time": nxt["time"]}
