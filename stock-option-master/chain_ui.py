"""
chain_ui.py — shared Streamlit rendering for the per-Greek exposure pages.
Keeps DEX / Vanna / Vega / Charm pages to a single call each.
"""

import pandas as pd
import streamlit as st

import chain_core as cc
import theme

# What a positive net / a zero-crossing means for each exposure.
INTERPRET = {
    "DEX": "Net dealer delta. The strike where cumulative DEX flips sign is the "
           "delta-neutral pivot; large |DEX| means dealers carry directional risk "
           "they must hedge — a tailwind/headwind for spot.",
    "GEX": "Net dealer gamma. Above the zero-gamma flip → dealers fade moves (pin); "
           "below → dealers chase (trend). Walls = biggest |GEX| strikes.",
    "VEX": "Vanna exposure — dealer delta added per +1 vol point. Big positive VEX "
           "means a falling VIX mechanically creates buying (the 'vanna rally').",
    "VEGA": "Vega exposure — dealer P&L sensitivity to a 1-pt vol change; where the "
            "book is most exposed to a vol spike/crush.",
    "CHARM": "Charm exposure — dealer delta drift per day as options decay. Sign hints "
             "the mechanical into-the-close drift (the afternoon ramp/fade).",
}


@st.cache_data(ttl=180, show_spinner="Loading chain…")
def _load(sym: str, stamp: str) -> dict:
    return cc.load_chain(sym)


def _scale_str(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"{v/1e9:+.2f} B"
    if a >= 1e6:
        return f"{v/1e6:+.0f} M"
    if a >= 1e3:
        return f"{v/1e3:+.0f} K"
    return f"{v:+.0f}"


def exposure_page(greek: str, page_title: str, icon: str):
    st.set_page_config(page_title=page_title, layout="wide")
    theme.apply()
    col, _mult, title, unit = cc.GREEK_SPECS[greek]
    st.title(page_title)
    st.caption(INTERPRET.get(greek, ""))

    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    sym = c1.selectbox("Symbol", ["ES", "NQ", "GC"], key=f"sym_{greek}")
    if c4.button(" Refresh", key=f"r_{greek}", width='stretch'):
        st.session_state[f"stamp_{greek}"] = pd.Timestamp.utcnow().strftime("%H%M%S")
    stamp = st.session_state.get(f"stamp_{greek}", "init")

    res = _load(sym, stamp)
    if res.get("error"):
        st.error(res["error"])
        return
    df, spot = res["df"], res["spot"]
    if sym == "GC":
        st.info("GC shown in **GLD terms** (≈ gold/10). Multiply strikes ×10 for the GC level.")

    exps = ["All (full book)"] + cc.expiries(df)
    exp = c2.selectbox("Expiry", exps, key=f"exp_{greek}")
    show3d = c3.toggle("3-D surface", value=False, key=f"d3_{greek}")
    dfx = cc.filter_exp(df, None if exp.startswith("All") else exp)

    agg = cc.aggregate_greek(dfx, spot, greek)
    total = float(agg["net"].sum()) if not agg.empty else 0.0
    # Zero-cross of cumulative exposure = that greek's flip strike.
    flip = None
    if not agg.empty:
        pos = agg[agg["cum"] >= 0]
        flip = float(pos["strike"].iloc[0]) if not pos.empty else None

    m = st.columns([1, 1, 1])
    m[0].metric("Spot", f"{spot:,.0f}")
    m[1].metric(f"Net {greek}", _scale_str(total), help=unit)
    m[2].metric(f"{greek} flip strike", f"{flip:,.0f}" if flip else "—",
                help="Where cumulative exposure crosses zero.")

    fig = cc.fig_exposure_bar(agg, spot, title, unit)
    if fig:
        st.plotly_chart(fig, use_container_width=True)

    if show3d:
        surf = cc.fig_exposure_surface(df, spot, greek)
        if surf:
            st.plotly_chart(surf, use_container_width=True)
        else:
            st.info("3-D surface needs multiple expiries / Plotly.")

    with st.expander("Per-strike table"):
        if not agg.empty:
            t = agg.copy()
            for cname in ("call", "put", "net", "cum"):
                t[cname] = (t[cname] / 1e6).round(2)
            t.columns = ["Strike", "Call (M)", "Put (M)", "Net (M)", "Cum (M)"]
            st.dataframe(t, hide_index=True, width='stretch')

    st.caption(" CBOE free feed: quotes ~15-min delayed, OI updates at the close. "
               "Dealer-sign is the long-call/short-put assumption (see chain_core). "
               "Levels show where reactions are likely — confirm with price action.")
