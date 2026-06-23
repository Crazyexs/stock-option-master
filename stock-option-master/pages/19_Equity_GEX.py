""" Equity GEX — gamma walls for any optionable ticker (SPY/QQQ/NVDA/…).

The same validated math as the ES/NQ/GC radar, generalised to single names and
ETFs. Call wall = dealer resistance, put wall = support, zero-gamma = the regime
pivot (above → pin/fade, below → trend). Heavily-retail meme names can flip the
dealer-sign assumption — read the caveat below.
"""
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Equity GEX", layout="wide")
import theme
theme.apply()

import equity_gex_core as eg

st.title("Equity GEX — Any Underlying")
st.caption("CBOE free CDN (~15-min delayed; OI updates after the close, so walls "
           "are the prior close's book — only spot is live).")

c1, c2, c3, c4 = st.columns([2, 1.4, 1.4, 1])
with c1:
    tk = st.selectbox("Ticker", eg.PRESETS, index=0,
                      help="Or type any optionable symbol below.")
    custom = st.text_input("…or custom ticker", value="").strip().upper()
    sym = custom or tk
with c2:
    horizon = st.radio("Horizon", ["daytrade", "swing"], horizontal=True)
with c3:
    rng = st.slider("Strike window ±%", 5, 30, 10) / 100.0
with c4:
    st.write("")
    if st.button("Refresh", type="primary"):
        st.cache_data.clear()


@st.cache_data(ttl=120, show_spinner="Computing GEX…")
def _load(sym, horizon, rng):
    return eg.compute(sym, horizon=horizon, strike_range=rng)


d = _load(sym, horizon, rng)
if d.get("error"):
    st.error(f"{sym}: {d['error']}")
    st.stop()

spot = d["spot"]
exp_lbl = f"{d['expiry']}" + (f" · {d['dte']}DTE" if d.get("dte") is not None else "")
st.markdown(f"**{sym}**{' (index)' if d['is_index'] else ''} — spot `{spot:,.2f}`  ·  "
            f"expiry `{exp_lbl}`  ·  {horizon}")

# Regime banner.
net = d.get("net_gex", 0.0)
if "POSITIVE" in d["regime"]:
    st.success(f"**{d['regime']}** · net GEX {net:,.0f} · {d['bias']}")
else:
    st.warning(f"**{d['regime']}** · net GEX {net:,.0f} · {d['bias']}")

# Level ladder.
def _row(label, val, tag):
    if val is None:
        return None
    diff = val - spot
    return {"Level": label, "Price": f"{val:,.2f}",
            "vs Spot": f"{diff:+,.2f}", "Note": tag}

ladder = [
    _row("Call Wall 2", d.get("secondary_call_wall"), "extra resistance"),
    _row("Call Wall", d.get("call_wall"), "main resistance / sell zone"),
    _row("Upper 1σ", d.get("upper_1sigma"), "expected-move ceiling"),
    _row("Zero Gamma", d.get("gamma_flip"), "regime pivot (above pin / below trend)"),
    _row("Lower 1σ", d.get("lower_1sigma"), "expected-move floor"),
    _row("Put Wall", d.get("put_wall"), "main support / buy zone"),
    _row("Put Wall 2", d.get("secondary_put_wall"), "extra support"),
]
ladder = [r for r in ladder if r]
if ladder:
    # Sort top-to-bottom by price for a real ladder.
    ladder.sort(key=lambda r: float(r["Price"].replace(",", "")), reverse=True)
    st.dataframe(pd.DataFrame(ladder), use_container_width=True, hide_index=True)

# Net GEX by strike.
agg = d.get("agg")
if isinstance(agg, pd.DataFrame) and not agg.empty:
    try:
        import plotly.graph_objects as go
        colors = [theme.GREEN if v >= 0 else theme.RED for v in agg["net_gex"]]
        fig = go.Figure(go.Bar(x=agg["strike"], y=agg["net_gex"], marker_color=colors))
        fig.add_vline(x=spot, line_dash="dash", line_color=theme.OFFWHITE,
                      annotation_text="spot")
        if d.get("gamma_flip"):
            fig.add_vline(x=d["gamma_flip"], line_dash="dot", line_color=theme.AMBER,
                          annotation_text="0γ")
        fig.update_layout(height=420, xaxis_title="strike",
                          yaxis_title="$ net dealer gamma / 1% move",
                          title=f"{sym} net GEX by strike")
        st.plotly_chart(theme.style_fig(fig), use_container_width=True)
    except Exception:
        st.dataframe(agg[["strike", "call_gex", "put_gex", "net_gex"]],
                     use_container_width=True, hide_index=True)

# Playbook.
if d.get("playbook"):
    st.markdown("#### Playbook")
    for line in d["playbook"]:
        st.markdown(f"- {line}")

st.caption("Dealer-sign assumption (long calls / short puts) is well-validated for "
           "index/ETF flow; on heavily-retail single names it can flip and the "
           "walls mislead. Confirm against price.")
