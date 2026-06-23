"""
yf_session.py — one shared, browser-impersonating HTTP session for yfinance.
================================================================================
Yahoo Finance aggressively rate-limits (HTTP 429) plain `requests`/urllib traffic,
especially from cloud/server IPs — which is why the app could 429 on the very
first load. The fix every yfinance user lands on is to drive yfinance through a
`curl_cffi` session that *impersonates a real Chrome browser* (TLS + HTTP/2
fingerprint), which Yahoo treats like a normal user. `curl_cffi` is already in
requirements.txt; this module is the missing wiring.

Use it everywhere instead of bare yfinance:

    import yf_session as yfs
    t = yfs.make_ticker("AAPL")          # like yf.Ticker("AAPL")
    df = yfs.download(["ES=F","NQ=F"], period="2d")

Both helpers degrade gracefully: if curl_cffi is missing or the installed
yfinance build doesn't accept a `session=` argument, they transparently fall
back to vanilla yfinance so nothing breaks.
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def session():
    """A process-wide curl_cffi session impersonating Chrome, or None if unavailable."""
    try:
        from curl_cffi import requests as _cffi
        return _cffi.Session(impersonate="chrome")
    except Exception:
        return None


def make_ticker(symbol: str):
    """yf.Ticker(symbol) routed through the impersonating session when possible."""
    import yfinance as yf
    s = session()
    if s is not None:
        try:
            return yf.Ticker(symbol, session=s)
        except TypeError:
            # Installed yfinance doesn't take session= (it bundles curl_cffi itself).
            pass
    return yf.Ticker(symbol)


def download(tickers, **kwargs):
    """yf.download(...) routed through the impersonating session when possible."""
    import yfinance as yf
    s = session()
    if s is not None:
        try:
            return yf.download(tickers, session=s, **kwargs)
        except TypeError:
            pass
    return yf.download(tickers, **kwargs)
