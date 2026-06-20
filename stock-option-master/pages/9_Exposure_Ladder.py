""" Exposure Ladder — every key level (walls + greek flips) on one price ladder."""
import pandas as pd
import streamlit as st

import chain_core as cc

st.set_page_config(page_title="Exposure Ladder", layout="wide")
import theme
theme.apply()
st.title(" Exposure Ladder")
st.caption("All the mechanical levels in one ladder: GEX call/put walls and the "
           "zero-cross (flip) of each greek's cumulative exposure.")


@st.cache_data(ttl=180, show_spinner="Loading chain…")
def _load(sym, stamp):
    return cc.load_chain(sym)


c1, c2, c3 = st.columns([1, 1, 1])
sym = c1.selectbox("Symbol", ["ES", "NQ", "GC"])
exp_choice = c2.empty()
if c3.button(" Refresh", width='stretch'):
    st.session_state["lad_stamp"] = pd.Timestamp.utcnow().strftime("%H%M%S")
res = _load(sym, st.session_state.get("lad_stamp", "init"))
if res.get("error"):
    st.error(res["error"])
    st.stop()
df, spot = res["df"], res["spot"]
exp = exp_choice.selectbox("Expiry", ["All (full book)"] + cc.expiries(df))
dfx = cc.filter_exp(df, None if exp.startswith("All") else exp)


def _flip(greek):
    agg = cc.aggregate_greek(dfx, spot, greek)
    if agg.empty:
        return None
    pos = agg[agg["cum"] >= 0]
    return float(pos["strike"].iloc[0]) if not pos.empty else None


# GEX walls.
gex = cc.aggregate_greek(dfx, spot, "GEX")
call_wall = put_wall = None
if not gex.empty:
    up = gex[gex["strike"] >= spot]
    dn = gex[gex["strike"] <= spot]
    if not up.empty and up["call"].max() > 0:
        call_wall = float(up.loc[up["call"].idxmax(), "strike"])
    if not dn.empty and dn["put"].min() < 0:
        put_wall = float(dn.loc[dn["put"].idxmin(), "strike"])

levels = [
    (" Call Wall", call_wall, "max call gamma — resistance / sell zone"),
    (" Gamma flip", _flip("GEX"), "pin↔trend regime pivot"),
    (" Delta flip", _flip("DEX"), "dealer delta-neutral pivot"),
    (" Charm flip", _flip("CHARM"), "into-close drift pivot"),
    (" Vanna flip", _flip("VEX"), "vol-rally pivot"),
    (" SPOT", spot, "current price"),
    (" Put Wall", put_wall, "max put gamma — support / buy zone"),
]
rows = [(n, v, t) for (n, v, t) in levels if isinstance(v, (int, float)) and v]
rows.sort(key=lambda r: r[1], reverse=True)
ladder = pd.DataFrame([{
    "Level": n, "Price": f"{v:,.2f}",
    "Δ vs spot": f"{(v-spot)/spot*100:+.2f}%",
    "Meaning": t} for (n, v, t) in rows])
st.dataframe(ladder, hide_index=True, width='stretch')

st.markdown("#### Net GEX by strike")
fig = cc.fig_exposure_bar(gex, spot, "Gamma Exposure (GEX)", "$ per 1% move")
if fig:
    st.plotly_chart(fig, use_container_width=True)
st.caption(" Levels reflect last-close OI (delayed feed). Confirm with price action.")
