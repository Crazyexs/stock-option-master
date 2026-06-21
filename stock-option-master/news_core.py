"""
news_core.py — live market headlines with a market-IMPACT classifier.
================================================================================
Pure-logic module (no Streamlit side effects). It does three things:

1.  FETCH headlines from fast, trader-grade sources:
      • FinancialJuice   — real-time macro/geopolitics squawk (RSS).
      • Walter Bloomberg  (@DeItaone on X) — the fastest English-language relay
        of Bloomberg terminal headlines, via a Nitter RSS mirror (best-effort:
        Nitter instances come and go, so several are tried in turn).
      • yfinance ticker news — per-symbol fallback so the page is never empty.

2.  RATE each headline's likely MARKET IMPACT — HIGH / MEDIUM / LOW — from a
    weighted keyword model, plus a coarse risk-on / risk-off tone and the macro
    categories it touches (rates, inflation, jobs, geopolitics, ...). This is the
    "does it move the market a lot or not" filter the desk actually wants: an
    FOMC decision or a Strait-of-Hormuz closure is HIGH; a single-name analyst
    note is LOW.

3.  MERGE + DEDUPE across sources and sort newest-first so the page can filter
    by impact and raise an alert the moment a HIGH-impact item lands.

WHY KEYWORDS, NOT AN LLM
------------------------
Squawk headlines are short, formulaic, and time-critical — a transparent,
auditable keyword model fires in microseconds with no API key, no rate limit,
and no hallucination. The weights below encode "what historically expands ES/NQ
vol": monetary-policy surprises, top-tier US data, and geopolitical shocks score
highest. Tune the maps; the thresholds are deliberately conservative.

HONEST LIMITATIONS
------------------
* Impact is a heuristic *prior*, not a realised move — confirm against price.
* Nitter mirrors are unofficial and frequently rate-limit or 404; if every
  instance is down, the Walter Bloomberg feed is simply skipped (the others
  still render). For a guaranteed feed, wire the official X API with a bearer
  token via `fetch_x_user(...)`.
* Headline tone (risk-on/off) is a bag-of-words guess; it cannot read sarcasm,
  negation, or "less bad than feared".
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

_UA = {"User-Agent": "Mozilla/5.0 (compatible; GEXTerminal/1.0)"}
_TIMEOUT = 12
_NITTER_TIMEOUT = 6     # mirrors are flaky — fail fast and move to the next one

# ── Sources ───────────────────────────────────────────────────────────────────
FINANCIALJUICE_RSS = "https://www.financialjuice.com/feed.ashx?xy=rss"

# Walter Bloomberg = @DeItaone. X has no public RSS, so we mirror through Nitter.
# Instances rotate often; the first that returns valid XML wins.
WALTER_BLOOMBERG_HANDLE = "DeItaone"
_NITTER_INSTANCES = (
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
    "https://nitter.cz",
    "https://nitter.1d4.us",
)

SOURCES = ("FinancialJuice", "Walter Bloomberg")


# ── Market-impact keyword model ───────────────────────────────────────────────
# weight ≥ 3 on its own ⇒ HIGH; the category tag drives the UI grouping.
# (pattern, weight, category). Patterns are matched case-insensitively as whole
# words / phrases against the headline.
_IMPACT_RULES: list[tuple[str, int, str]] = [
    # ── Monetary policy (the single biggest vol driver) ──
    (r"\bfomc\b", 4, "rates"), (r"\bfed\b", 3, "rates"),
    (r"\bpowell\b", 3, "rates"), (r"\brate (?:decision|cut|hike|rise)\b", 4, "rates"),
    (r"\binterest rate", 3, "rates"), (r"\brate cut", 3, "rates"),
    (r"\brate hike", 3, "rates"), (r"\bbasis points?\b", 2, "rates"),
    (r"\b(?:ecb|boj|pboc|boe)\b", 3, "rates"), (r"\bjackson hole\b", 3, "rates"),
    (r"\bdot plot\b", 3, "rates"), (r"\bquantitative (?:easing|tightening)\b", 3, "rates"),
    (r"\bbeige book\b", 2, "rates"), (r"\bfed (?:minutes|speaker|official)\b", 2, "rates"),
    # ── Top-tier US data ──
    (r"\bcpi\b", 4, "inflation"), (r"\binflation\b", 3, "inflation"),
    (r"\bpce\b", 3, "inflation"), (r"\bppi\b", 3, "inflation"),
    (r"\bnonfarm\b", 4, "jobs"), (r"\bpayroll", 4, "jobs"),
    (r"\bnfp\b", 4, "jobs"), (r"\bjobs report\b", 3, "jobs"),
    (r"\bunemployment\b", 3, "jobs"), (r"\bjobless claims\b", 2, "jobs"),
    (r"\bgdp\b", 3, "growth"), (r"\brecession\b", 3, "growth"),
    (r"\b(?:ism|pmi)\b", 2, "growth"), (r"\bretail sales\b", 2, "growth"),
    (r"\bconsumer confidence\b", 2, "growth"), (r"\bdurable goods\b", 2, "growth"),
    # ── Geopolitics / shocks ──
    (r"\bwar\b", 3, "geopolitics"), (r"\binvad", 3, "geopolitics"),
    (r"\bmissile", 3, "geopolitics"), (r"\bnuclear\b", 3, "geopolitics"),
    (r"\bairstrike|air strike\b", 3, "geopolitics"), (r"\battack", 2, "geopolitics"),
    (r"\bceasefire\b", 3, "geopolitics"), (r"\bsanction", 2, "geopolitics"),
    (r"\bstrait of hormuz\b", 4, "geopolitics"), (r"\bopec\b", 3, "energy"),
    (r"\boil (?:price|production|output)\b", 2, "energy"),
    (r"\btariff", 3, "trade"), (r"\btrade war\b", 3, "trade"),
    (r"\bembargo\b", 2, "trade"),
    # ── Credit / systemic ──
    (r"\bdefault\b", 3, "credit"), (r"\bdebt ceiling\b", 3, "credit"),
    (r"\bdowngrade", 2, "credit"), (r"\bcredit rating\b", 2, "credit"),
    (r"\bbankruptcy\b", 3, "credit"), (r"\bcircuit breaker\b", 4, "systemic"),
    (r"\bbailout\b", 3, "systemic"), (r"\bcontagion\b", 3, "systemic"),
    (r"\bemergency\b", 2, "systemic"), (r"\bhalt(?:ed|s)?\b", 1, "systemic"),
    # ── Mega-cap single names (NQ leaders) ──
    (r"\b(?:nvidia|nvda)\b", 2, "megacap"), (r"\b(?:apple|aapl)\b", 2, "megacap"),
    (r"\b(?:microsoft|msft)\b", 2, "megacap"), (r"\b(?:tesla|tsla)\b", 2, "megacap"),
    (r"\b(?:amazon|amzn)\b", 2, "megacap"), (r"\b(?:meta|googl|alphabet)\b", 2, "megacap"),
    (r"\bearnings\b", 1, "earnings"), (r"\bguidance\b", 1, "earnings"),
]

# Tone lexicon — coarse risk-on (+) vs risk-off (−) lean of a headline.
_RISK_ON = (r"\bbeat", r"\bbeats\b", r"\braise", r"\bsurge", r"\bjump", r"\brally",
            r"\bsoar", r"\bgains?\b", r"\bstimulus\b", r"\beas(?:e|ing)\b",
            r"\bcools?\b", r"\bcooler\b", r"\bdovish\b", r"\bupgrade", r"\boptimis")
_RISK_OFF = (r"\bmiss", r"\bmisses\b", r"\bcut guidance", r"\bplunge", r"\bplummet",
             r"\bcrash", r"\bslump", r"\bfalls?\b", r"\bdrops?\b", r"\bhawkish\b",
             r"\bhotter\b", r"\bhot\b", r"\bspike", r"\bwar\b", r"\battack",
             r"\bdowngrade", r"\bdefault\b", r"\bfear", r"\bselloff|sell-off\b",
             r"\brecession\b", r"\bsanction", r"\bescalat")

_HIGH_THRESHOLD = 3
_MEDIUM_THRESHOLD = 1


def classify_impact(title: str) -> dict:
    """
    Rate one headline. Returns:
        {impact: HIGH|MEDIUM|LOW, score, categories: [...], tone: risk-on|risk-off|neutral}
    """
    t = (title or "").lower()
    score = 0
    cats: list[str] = []
    for pat, w, cat in _IMPACT_RULES:
        if re.search(pat, t):
            score += w
            if cat not in cats:
                cats.append(cat)
    if score >= _HIGH_THRESHOLD:
        impact = "HIGH"
    elif score >= _MEDIUM_THRESHOLD:
        impact = "MEDIUM"
    else:
        impact = "LOW"

    on = sum(1 for p in _RISK_ON if re.search(p, t))
    off = sum(1 for p in _RISK_OFF if re.search(p, t))
    if off > on:
        tone = "risk-off"
    elif on > off:
        tone = "risk-on"
    else:
        tone = "neutral"

    return {"impact": impact, "score": score, "categories": cats, "tone": tone}


# ── RSS / Atom parsing (stdlib only — no feedparser dependency) ────────────────
def _strip_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()   # drop XML namespace


def _parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)            # RFC-822 (RSS)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _clean(text: str | None) -> str:
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)             # drop any HTML
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(xml_text: str, source: str) -> list[dict]:
    """Parse an RSS or Atom document into a list of raw item dicts."""
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return items
    for el in root.iter():
        if _strip_tag(el.tag) not in ("item", "entry"):
            continue
        title = link = pub = ""
        for child in el:
            name = _strip_tag(child.tag)
            if name == "title" and not title:
                title = _clean(child.text)
            elif name == "link" and not link:
                link = (child.get("href") or child.text or "").strip()
            elif name in ("pubdate", "published", "updated") and not pub:
                pub = (child.text or "").strip()
        if not title:
            continue
        items.append({"title": title, "link": link, "source": source,
                      "published": _parse_dt(pub)})
    return items


# ── Fetchers (best-effort; never raise) ───────────────────────────────────────
def fetch_financialjuice() -> list[dict]:
    try:
        r = requests.get(FINANCIALJUICE_RSS, headers=_UA, timeout=_TIMEOUT)
        r.raise_for_status()
        items = parse_feed(r.text, "FinancialJuice")
        for it in items:                            # drop the "FinancialJuice: " prefix
            it["title"] = re.sub(r"^FinancialJuice:\s*", "", it["title"])
        return items
    except Exception:
        return []


def fetch_walter_bloomberg() -> list[dict]:
    """Try each Nitter mirror until one returns valid XML; else empty."""
    for base in _NITTER_INSTANCES:
        url = f"{base}/{WALTER_BLOOMBERG_HANDLE}/rss"
        try:
            r = requests.get(url, headers=_UA, timeout=_NITTER_TIMEOUT)
            if r.status_code != 200 or "<" not in r.text[:200]:
                continue
            items = parse_feed(r.text, "Walter Bloomberg")
            if items:
                # Strip Nitter's "RT by @x:" / "R to @x:" relay-noise prefixes.
                for it in items:
                    it["title"] = re.sub(r"^(?:RT by|R to) @\w+:\s*", "", it["title"])
                return items
        except Exception:
            continue
    return []


def fetch_x_user(handle: str, bearer_token: str, max_results: int = 25) -> list[dict]:
    """
    Official X API v2 fallback (guaranteed feed if Nitter is down). Requires a
    bearer token with read access. Best-effort: returns [] on any failure.
    """
    try:
        u = requests.get(f"https://api.twitter.com/2/users/by/username/{handle}",
                         headers={"Authorization": f"Bearer {bearer_token}"},
                         timeout=_TIMEOUT)
        u.raise_for_status()
        uid = u.json()["data"]["id"]
        tw = requests.get(
            f"https://api.twitter.com/2/users/{uid}/tweets",
            headers={"Authorization": f"Bearer {bearer_token}"},
            params={"max_results": min(max(max_results, 5), 100),
                    "tweet.fields": "created_at", "exclude": "retweets,replies"},
            timeout=_TIMEOUT)
        tw.raise_for_status()
        out = []
        for t in tw.json().get("data", []):
            out.append({"title": _clean(t.get("text", "")),
                        "link": f"https://x.com/{handle}/status/{t.get('id')}",
                        "source": "Walter Bloomberg",
                        "published": _parse_dt(t.get("created_at"))})
        return out
    except Exception:
        return []


def fetch_yf_ticker_news(ticker: str) -> list[dict]:
    """Per-symbol Yahoo news (handles both flat and {'content': {...}} schemas)."""
    try:
        import yfinance as yf
        raw = yf.Ticker(ticker).news or []
    except Exception:
        return []
    out = []
    for it in raw:
        c = it.get("content", it)
        title = c.get("title") or it.get("title")
        if not title:
            continue
        link = (c.get("canonicalUrl", {}) or {}).get("url") or it.get("link", "")
        pub = c.get("pubDate") or it.get("providerPublishTime")
        if isinstance(pub, (int, float)):
            published = datetime.fromtimestamp(pub, tz=timezone.utc)
        else:
            published = _parse_dt(pub)
        out.append({"title": _clean(title), "link": link,
                    "source": f"Yahoo · {ticker}", "published": published})
    return out


# ── Aggregation ───────────────────────────────────────────────────────────────
_IMPACT_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for it in items:
        key = re.sub(r"[^a-z0-9]", "", (it.get("title") or "").lower())[:80]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def get_headlines(sources: list[str] | None = None,
                  yf_ticker: str | None = None,
                  min_impact: str = "LOW",
                  limit: int = 60) -> list[dict]:
    """
    Fetch + classify + merge headlines, newest first.

    sources    : subset of SOURCES to pull (default: all).
    yf_ticker  : also pull per-symbol Yahoo news for this ticker (optional).
    min_impact : drop items below this impact (LOW|MEDIUM|HIGH).
    Returns list of dicts: title, link, source, published(datetime|None),
                            impact, score, categories, tone, age_min.
    """
    sources = sources or list(SOURCES)
    raw: list[dict] = []
    if "FinancialJuice" in sources:
        raw += fetch_financialjuice()
    if "Walter Bloomberg" in sources:
        raw += fetch_walter_bloomberg()
    if yf_ticker:
        raw += fetch_yf_ticker_news(yf_ticker)

    raw = _dedupe(raw)
    for it in raw:
        it.update(classify_impact(it["title"]))

    floor = _IMPACT_ORDER.get(min_impact.upper(), 2)
    raw = [it for it in raw if _IMPACT_ORDER[it["impact"]] <= floor]

    now = datetime.now(timezone.utc)
    for it in raw:
        pub = it.get("published")
        it["age_min"] = (now - pub).total_seconds() / 60.0 if pub else None

    # Sort newest-first; undated items sink to the bottom.
    raw.sort(key=lambda it: it.get("published") or datetime.min.replace(tzinfo=timezone.utc),
             reverse=True)
    return raw[:limit]


def high_impact_alerts(items: list[dict], within_min: float = 30.0) -> list[dict]:
    """HIGH-impact headlines from the last `within_min` minutes (for the banner)."""
    out = []
    for it in items:
        if it.get("impact") != "HIGH":
            continue
        age = it.get("age_min")
        if age is None or age <= within_min:
            out.append(it)
    return out


def market_impact_summary(items: list[dict]) -> dict:
    """Aggregate read for the model: how hot is the tape right now?"""
    highs = [it for it in items if it.get("impact") == "HIGH"]
    recent_high = high_impact_alerts(items, within_min=60.0)
    off = sum(1 for it in highs if it.get("tone") == "risk-off")
    on = sum(1 for it in highs if it.get("tone") == "risk-on")
    if recent_high:
        level = "elevated"
    elif highs:
        level = "watch"
    else:
        level = "calm"
    tone = "risk-off" if off > on else ("risk-on" if on > off else "mixed")
    cats: list[str] = []
    for it in recent_high:
        for c in it.get("categories", []):
            if c not in cats:
                cats.append(c)
    return {"level": level, "n_high": len(highs), "n_recent_high": len(recent_high),
            "tone": tone, "categories": cats}
