""" Volatility Heatmap — IV surface (skew × term structure), 2-D + 3-D."""
import pandas as pd
import streamlit as st

import chain_core as cc

st.set_page_config(page_title="Vol Heatmap", layout="wide")
import theme
theme.apply()
st.title(" Implied-Vol Heatmap & Surface")
st.caption("IV across strike (skew) and DTE (term structure). Steep left-side skew "
           "= downside fear; inverted term structure (front IV > back) = event/stress.")


@st.cache_data(ttl=180, show_spinner="Loading chain…")
def _load(sym, stamp):
    return cc.load_chain(sym, strike_range=0.20)


c1, c2, c3 = st.columns([1, 1, 1])
sym = c1.selectbox("Symbol", ["ES", "NQ", "GC"])
mode = c2.radio("View", ["Heatmap (2-D)", "Surface (3-D)"], horizontal=True)
if c3.button(" Refresh", width='stretch'):
    st.session_state["vol_stamp"] = pd.Timestamp.utcnow().strftime("%H%M%S")
res = _load(sym, st.session_state.get("vol_stamp", "init"))
if res.get("error"):
    st.error(res["error"])
    st.stop()
df, spot = res["df"], res["spot"]

fig = cc.fig_iv_heatmap(df) if mode.startswith("Heatmap") else cc.fig_iv_surface(df)
if fig:
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Not enough IV data to plot (or Plotly missing).")

# Skew line for the nearest expiry.
exps = cc.expiries(df)
if exps:
    exp = st.selectbox("Skew for expiry", exps)
    sk = df[df["exp"] == exp].groupby("strike")["iv"].mean()
    if not sk.empty:
        st.line_chart(sk.rename("IV %"))
        st.caption(f"IV skew at {exp} — spot ≈ {spot:,.0f}.")
