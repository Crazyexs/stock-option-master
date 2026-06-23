""" Vol Regime — VIX term structure (VIX9D / VIX / VIX3M / VVIX).

Contango = calm/grind; backwardation = stress/expansion. Pair the term-structure
read with the GEX sign: backwardation + negative GEX is the high-conviction trend
regime; contango + positive GEX is the pin/fade regime.
"""
import streamlit as st

st.set_page_config(page_title="Vol Regime", layout="wide")
import theme
theme.apply()

import vol_regime_core as vr

st.title("Volatility Regime — VIX Term Structure")
st.caption("The shape of the VIX curve is a regime filter that confirms or vetoes "
           "the GEX read. Data: CBOE free CDN (~15-min delayed).")

if st.button("Refresh", type="primary"):
    st.cache_data.clear()


@st.cache_data(ttl=120, show_spinner="Fetching VIX complex…")
def _load():
    return vr.get_regime()


r = _load()
lv = r["levels"]

cols = st.columns(4)
order = [("VIX9D", "9-day (front)"), ("VIX", "30-day"),
         ("VIX3M", "3-month (back)"), ("VVIX", "vol-of-vol")]
for i, (k, lbl) in enumerate(order):
    v = lv.get(k)
    cols[i].metric(lbl, f"{v:.2f}" if v is not None else "n/a")

st.divider()
m = st.columns(2)
m[0].metric("VIX9D / VIX (front slope)",
            f"{r['front_ratio']:.3f}" if r["front_ratio"] else "n/a",
            help="<1 short-end contango; >1 front-month bid (fear hitting).")
m[1].metric("VIX / VIX3M (term slope)",
            f"{r['term_ratio']:.3f}" if r["term_ratio"] else "n/a",
            delta=r["structure"],
            help=">1 = backwardation (stress); <1 = contango (calm).")

regime = r["regime"]
if "STRESS" in regime:
    st.error(f"**{regime}** — {r['read']}")
elif "CALM" in regime:
    st.success(f"**{regime}** — {r['read']}")
else:
    st.warning(f"**{regime}** — {r['read']}")

if r.get("vvix_note"):
    st.info(r["vvix_note"])

# Term-structure curve.
pts = [(lbl, lv.get(k)) for k, lbl in
       (("VIX9D", "9D"), ("VIX", "30D"), ("VIX3M", "3M")) if lv.get(k)]
if len(pts) >= 2:
    try:
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_scatter(x=[p[0] for p in pts], y=[p[1] for p in pts],
                        mode="lines+markers", line=dict(color=theme.AMBER, width=3),
                        marker=dict(size=10))
        fig.update_layout(height=360, xaxis_title="tenor", yaxis_title="implied vol",
                          title="VIX term structure")
        st.plotly_chart(theme.style_fig(fig), use_container_width=True)
    except Exception:
        pass

with st.expander("How to read this with GEX"):
    st.markdown(
        "- **Backwardation (VIX/VIX3M > 1) + NEGATIVE net GEX** → strongest trend "
        "/ expansion regime. Trade momentum; do not fade walls.\n"
        "- **Contango (VIX/VIX3M < 1) + POSITIVE net GEX** → grind / pin. Fade the "
        "edges toward zero-gamma; sell rallies into the call wall, buy dips into "
        "the put wall.\n"
        "- **Curve flat / signals disagree** → lower conviction, wait for price to "
        "resolve at a GEX level.")
