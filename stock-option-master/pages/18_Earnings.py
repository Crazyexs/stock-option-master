""" Earnings Calendar — the IV-crush guard for the single-name option tools.

The Trade Finder / scanner are blind to earnings. Buying premium into a print
pays peak IV then eats the crush; selling into it is uncapped event risk. Check a
ticker against your DTE window, or scan a watchlist for upcoming reports.
"""
from datetime import date
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Earnings", layout="wide")
import theme
theme.apply()

import earnings_core as ec

st.title("Earnings Calendar")
st.caption("Earnings dominate single-name vol. Dates are estimates until "
           "confirmed (treat as ±1 session). Data: yfinance.")

tab_one, tab_list = st.tabs(["Check one ticker vs DTE window", "Watchlist scan"])

# ── Single ticker vs window ───────────────────────────────────────────────────
with tab_one:
    c1, c2, c3 = st.columns(3)
    with c1:
        tk = st.text_input("Ticker", value="NVDA").strip().upper() or "NVDA"
    with c2:
        dmin = st.number_input("Min DTE", value=20, min_value=1)
    with c3:
        dmax = st.number_input("Max DTE", value=60, min_value=2)

    if st.button("Check", type="primary"):
        with st.spinner(f"Looking up {tk} earnings…"):
            r = ec.earnings_in_window(tk, int(dmin), int(dmax))
        if not r.get("date"):
            st.info(f"No upcoming earnings date found for {tk}.")
        else:
            cols = st.columns(3)
            cols[0].metric("Next earnings", r["date"])
            cols[1].metric("Days away", r["days_away"])
            cols[2].metric("In DTE window?", "YES" if r["in_window"] else "no")
            if r["warning"]:
                (st.error if r["in_window"] else st.warning)(r["warning"])
            else:
                st.success(f"{tk} earnings are clear of the {int(dmin)}-{int(dmax)} "
                           "DTE window — no event-IV penalty from this print.")

# ── Watchlist scan ────────────────────────────────────────────────────────────
with tab_list:
    default = "AAPL, MSFT, NVDA, TSLA, AMZN, META, GOOGL, AMD, NFLX, JPM"
    raw = st.text_area("Watchlist (comma-separated)", value=default, height=80)
    horizon = st.slider("Horizon (days)", 7, 90, 45)
    tickers = [t.strip().upper() for t in raw.replace("\n", ",").split(",") if t.strip()]

    if st.button("Scan watchlist", type="primary"):
        with st.spinner(f"Scanning {len(tickers)} tickers…"):
            rows = ec.watchlist_calendar(tickers, horizon_days=horizon)
        if not rows:
            st.info("No earnings found in the horizon (or yfinance rate-limited — retry).")
        else:
            df = pd.DataFrame([{"Ticker": r["ticker"], "Earnings": r["date"],
                                "Days away": r["days_away"]} for r in rows])
            st.dataframe(df, use_container_width=True, hide_index=True)
            soon = [r for r in rows if r["days_away"] <= 7]
            if soon:
                st.warning("Reporting within 7 days: "
                           + ", ".join(f"{r['ticker']} ({r['days_away']}d)" for r in soon)
                           + " — expect IV ramp then crush.")
