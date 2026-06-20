""" Open Interest — OI / volume by strike, calls vs puts, with P/C ratios."""
import pandas as pd
import streamlit as st

import chain_core as cc

st.set_page_config(page_title="Open Interest", layout="wide")
import theme
theme.apply()
st.title(" Open Interest & Volume")
st.caption("Where the contracts actually sit. Big OI strikes are magnets; a volume "
           "spike at a strike vs its OI flags fresh positioning (today's flow).")


@st.cache_data(ttl=180, show_spinner="Loading chain…")
def _load(sym, stamp):
    return cc.load_chain(sym, strike_range=0.20)


c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
sym = c1.selectbox("Symbol", ["ES", "NQ", "GC"])
metric = c2.radio("Show", ["oi", "volume"], horizontal=True)
if c4.button(" Refresh", width='stretch'):
    st.session_state["oi_stamp"] = pd.Timestamp.utcnow().strftime("%H%M%S")
res = _load(sym, st.session_state.get("oi_stamp", "init"))
if res.get("error"):
    st.error(res["error"])
    st.stop()
df, spot = res["df"], res["spot"]
exp = c3.selectbox("Expiry", ["All"] + cc.expiries(df))
view = df if exp == "All" else df[df["exp"] == exp]

call_oi, put_oi = view[view["type"] == "C"]["oi"].sum(), view[view["type"] == "P"]["oi"].sum()
call_v, put_v = view[view["type"] == "C"]["volume"].sum(), view[view["type"] == "P"]["volume"].sum()
m = st.columns(4)
m[0].metric("Spot", f"{spot:,.0f}")
m[1].metric("Put/Call OI", f"{(put_oi/call_oi):.2f}" if call_oi else "—",
            help=">1 = more put OI (defensive / potential support fuel).")
m[2].metric("Put/Call Vol", f"{(put_v/call_v):.2f}" if call_v else "—")
big = view.loc[view["oi"].idxmax()] if not view.empty else None
m[3].metric("Largest OI strike", f"{big['strike']:,.0f} {big['type']}" if big is not None else "—")

fig = cc.fig_oi(view, spot, value=metric)
if fig:
    st.plotly_chart(fig, use_container_width=True)
st.caption(" OI updates only at the close; intraday the volume view is the live one.")
