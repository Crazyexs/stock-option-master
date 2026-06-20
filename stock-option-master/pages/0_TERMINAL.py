""" TERMINAL — Bloomberg-style cockpit: monitor, function menu, GEX overview."""
import pandas as pd
import streamlit as st

import theme
import gex_core as gx

st.set_page_config(page_title="TERMINAL", layout="wide")
theme.apply()

st.markdown("#  QUANT OPTIONS TERMINAL")
st.caption("Bloomberg-styled cockpit — amber/dark UI, live tape & monitor, one-key "
           "access to every function. (Styled after the Terminal; not affiliated with "
           "Bloomberg and not a replica of its data/functions.)")

left, right = st.columns([2, 3])

# ── Market monitor ────────────────────────────────────────────────────────────
with left:
    st.markdown("### MARKET MONITOR")
    theme.market_monitor()

# ── Function menu (Bloomberg <GO> style) ──────────────────────────────────────
FUNCTIONS = [
    ("GEX",  "pages/1_GEX_Day_Trade.py",   "Gamma walls / flip day-trade levels"),
    ("NQ",   "pages/2_NQ_Macro_Bias.py",    "NQ cross-asset + macro bias"),
    ("DEX",  "pages/3_DEX_Flow.py",         "Delta exposure flow"),
    ("VEX",  "pages/4_Vanna_VEX.py",        "Vanna exposure"),
    ("VEGA", "pages/5_Vega_Exposure.py",     "Vega exposure"),
    ("CHRM", "pages/6_Charm.py",             "Charm / decay drift"),
    ("CHN",  "pages/7_Option_Chain.py",      "Full option chain + Greeks"),
    ("VOL",  "pages/8_Vol_Heatmap.py",       "IV heatmap + 3-D surface"),
    ("LAD",  "pages/9_Exposure_Ladder.py",   "All key levels ladder"),
    ("OI",   "pages/10_Open_Interest.py",    "Open interest / volume"),
    ("CHT",  "pages/11_Contract_Chart.py",   "Price candles + GEX overlay"),
    ("N",    "pages/12_Macro_News.py",        "Events calendar + headlines"),
]
with right:
    st.markdown("### FUNCTIONS  ·  click <GO>")
    cols = st.columns(2)
    for i, (code, path, desc) in enumerate(FUNCTIONS):
        with cols[i % 2]:
            try:
                st.page_link(path, label=f"{code} <GO> — {desc}")
            except Exception:
                st.markdown(f"<span class='bbg-fn'>{code} &lt;GO&gt;</span> — {desc}",
                            unsafe_allow_html=True)

st.divider()

# ── GEX overview strip ────────────────────────────────────────────────────────
st.markdown("### GAMMA OVERVIEW  ·  ES / NQ / GC")


@st.cache_data(ttl=120, show_spinner="Computing GEX…")
def _gex(stamp):
    return gx.compute_all()


c1, _ = st.columns([1, 5])
if c1.button(" Refresh", width='stretch'):
    st.session_state["term_stamp"] = pd.Timestamp.utcnow().strftime("%H%M%S")
res = _gex(st.session_state.get("term_stamp", "init"))

cols = st.columns(3)
for col, sym in zip(cols, ("ES", "NQ", "GC")):
    d = res.get(sym, {})
    with col:
        st.markdown(f"#### {sym}")
        if d.get("error"):
            st.error(d["error"])
            continue
        spot, flip = d.get("spot"), d.get("gamma_flip")
        pos = (spot >= flip) if (flip and spot) else (d.get("net_gex", 0) >= 0)
        regime = " PIN" if pos else " TREND"
        st.metric("Spot", f"{spot:,.0f}" if spot else "—")
        m = st.columns(2)
        m[0].metric("Zero γ", f"{flip:,.0f}" if flip else "—")
        m[1].metric("Regime", regime)
        m2 = st.columns(2)
        m2[0].metric("Call wall", f"{d.get('call_wall'):,.0f}" if d.get("call_wall") else "—")
        m2[1].metric("Put wall", f"{d.get('put_wall'):,.0f}" if d.get("put_wall") else "—")

st.caption(" Styled after Bloomberg for familiarity only. Data = CBOE free feed "
           "(~15-min delayed, OI at close) + yfinance. Not investment advice.")
