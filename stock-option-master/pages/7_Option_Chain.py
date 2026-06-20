""" Option Chain — full CBOE chain with every Greek, filterable."""
import pandas as pd
import streamlit as st

import chain_core as cc

st.set_page_config(page_title="Option Chain", layout="wide")
import theme
theme.apply()
st.title(" Option Chain")
st.caption("Full CBOE chain (delta, gamma, vega, theta + computed vanna & charm). "
           "Quotes ~15-min delayed; OI updates at the close.")


@st.cache_data(ttl=180, show_spinner="Loading chain…")
def _load(sym, stamp):
    return cc.load_chain(sym, strike_range=0.20)


c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
sym = c1.selectbox("Symbol", ["ES", "NQ", "GC"])
if c4.button(" Refresh", width='stretch'):
    st.session_state["chain_stamp"] = pd.Timestamp.utcnow().strftime("%H%M%S")
res = _load(sym, st.session_state.get("chain_stamp", "init"))
if res.get("error"):
    st.error(res["error"])
    st.stop()
df, spot = res["df"], res["spot"]

exp = c2.selectbox("Expiry", ["All"] + cc.expiries(df))
side = c3.selectbox("Type", ["Both", "Calls", "Puts"])
view = df if exp == "All" else df[df["exp"] == exp]
if side == "Calls":
    view = view[view["type"] == "C"]
elif side == "Puts":
    view = view[view["type"] == "P"]

m = st.columns(4)
m[0].metric("Spot", f"{spot:,.2f}")
m[1].metric("Contracts", f"{len(view):,}")
call_oi = df[df["type"] == "C"]["oi"].sum()
put_oi = df[df["type"] == "P"]["oi"].sum()
m[2].metric("Put/Call OI", f"{(put_oi/call_oi):.2f}" if call_oi else "—")
call_v = df[df["type"] == "C"]["volume"].sum()
put_v = df[df["type"] == "P"]["volume"].sum()
m[3].metric("Put/Call Vol", f"{(put_v/call_v):.2f}" if call_v else "—")

show = view.sort_values(["exp", "strike", "type"]).copy()
show["iv"] = show["iv"].round(1)
for cprec in ("delta", "gamma", "vanna", "charm"):
    show[cprec] = show[cprec].round(4)
show["vega"] = show["vega"].round(2)
show["theta"] = show["theta"].round(2)
show = show[["exp", "dte", "strike", "type", "oi", "volume", "iv",
             "delta", "gamma", "vega", "theta", "vanna", "charm"]]
show.columns = ["Expiry", "DTE", "Strike", "Type", "OI", "Vol", "IV%",
                "Delta", "Gamma", "Vega", "Theta", "Vanna", "Charm"]
st.dataframe(show, hide_index=True, width='stretch', height=620)
st.caption(" Dealer-sign exposures elsewhere assume long-call/short-put (see chain_core).")
