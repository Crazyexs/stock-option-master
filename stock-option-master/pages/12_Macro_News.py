""" Macro News — US event calendar (FOMC/NFP) + live headlines for risk assets."""
from datetime import date
import streamlit as st

import macro_core as mc

st.set_page_config(page_title="Macro News", layout="wide")
import theme
theme.apply()
st.title(" Macro News & Event Calendar")
st.caption("The scheduled events that break gamma pins, plus live headlines. Near a "
           "red event: vol expands, walls fail — size down or wait for the post-event regime.")

# ── Event calendar ────────────────────────────────────────────────────────────
st.markdown("####  Upcoming high-impact US events")
ev = mc.event_risk()
if ev.get("event"):
    flag = ev["flag"]
    head = f"**Next:** {ev['event']} — {ev['date']} ({ev['days']}d away)"
    if flag in ("TODAY", "tomorrow"):
        st.error(" " + head + f" · **{flag.upper()}** — gamma pins break, reduce size.")
    elif flag == "this week":
        st.warning(" " + head + " · this week — expect vol expansion.")
    else:
        st.info(" " + head)
    for d, name in ev.get("upcoming", []):
        days = (mc._parse(d) - date.today()).days
        st.markdown(f"- **{d}** · {name} · {days}d")
st.caption("FOMC dates are static — verify vs federalreserve.gov; CPI not auto-listed.")

st.divider()

# ── Headlines ─────────────────────────────────────────────────────────────────
st.markdown("####  Live headlines")
TICKERS = {"S&P 500 (SPY)": "SPY", "Nasdaq-100 (QQQ)": "QQQ",
           "Gold (GLD)": "GLD", "Volatility (^VIX)": "^VIX",
           "US Dollar (UUP)": "UUP"}
choice = st.selectbox("Feed", list(TICKERS.keys()))
tk = TICKERS[choice]


@st.cache_data(ttl=600, show_spinner="Fetching headlines…")
def _news(ticker):
    try:
        import yfinance as yf
        return yf.Ticker(ticker).news or []
    except Exception as exc:
        return [{"_error": str(exc)}]


items = _news(tk)
if items and items[0].get("_error"):
    st.warning(f"News unavailable: {items[0]['_error']}")
elif not items:
    st.info("No headlines returned for this feed right now.")
else:
    for it in items[:15]:
        # yfinance news schema varies; handle both flat and {'content': {...}} forms.
        content = it.get("content", it)
        title = content.get("title") or it.get("title") or "(untitled)"
        pub = (content.get("provider", {}) or {}).get("displayName") or it.get("publisher", "")
        link = (content.get("canonicalUrl", {}) or {}).get("url") or it.get("link", "")
        if link:
            st.markdown(f"- [{title}]({link})  ·  *{pub}*")
        else:
            st.markdown(f"- {title}  ·  *{pub}*")

st.caption("Headlines via yfinance (best-effort, schema varies). For a trading feed, "
           "wire a dedicated news API key here.")
