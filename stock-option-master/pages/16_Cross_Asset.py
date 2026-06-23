""" Cross-Asset Macro — the context the index tape trades inside.

Rates (and the 3m10y curve), the dollar, oil, gold, crypto, the VIX, and the
S&P sector ETFs ranked by the day's move. Tech is long-duration: yields up =
multiple compression. Sector leadership (cyclical vs defensive) confirms or
diverges from the GEX-implied bias.
"""
import streamlit as st

st.set_page_config(page_title="Cross-Asset", layout="wide")
import theme
theme.apply()

import crossasset_core as ca

st.title("Cross-Asset Macro Dashboard")
st.caption("Daily close-to-close context (yfinance). Treat as the day's lean, "
           "confirmed by price action at your GEX levels.")

if st.button("Refresh", type="primary"):
    st.cache_data.clear()


@st.cache_data(ttl=300, show_spinner="Loading cross-asset board…")
def _load():
    return ca.dashboard()


d = _load()


def _delta(r):
    if r.get("is_yield"):
        return f"{r['chg']:+.3f} pp"
    return f"{r.get('pct', 0):+.2f}%"


# ── Rates ─────────────────────────────────────────────────────────────────────
st.markdown("#### Rates & the curve")
y = d["yields"]
if y["rows"]:
    cols = st.columns(len(y["rows"]) + 1)
    for i, r in enumerate(y["rows"]):
        cols[i].metric(r["label"], f"{r['last']:.2f}%", _delta(r))
    slope = y["slope_3m10y_bp"]
    cols[-1].metric("3m10y curve", f"{slope:+.0f} bp" if slope is not None else "n/a",
                    y["curve"], delta_color="off")
    if slope is not None and slope < 0:
        st.warning(f"Curve inverted ({slope:+.0f} bp 3m10y) — classic late-cycle / "
                   "recession-timing signal. Rate-cut repricing drives big NQ swings.")
else:
    st.info("Rates feed unavailable.")

st.divider()

# ── Macro board ───────────────────────────────────────────────────────────────
st.markdown("#### Index futures · dollar · commodities · crypto · vol")
macro = d["macro"]
if macro:
    per_row = 4
    for start in range(0, len(macro), per_row):
        cols = st.columns(per_row)
        for j, r in enumerate(macro[start:start + per_row]):
            val = f"{r['last']:,.2f}"
            cols[j].metric(r["label"], val, _delta(r))
else:
    st.info("Macro feed unavailable (markets closed or yfinance rate-limited).")

st.divider()

# ── Sectors ───────────────────────────────────────────────────────────────────
st.markdown("#### S&P sectors — risk-on/off leadership")
sec = d["sectors"]
if sec["rows"]:
    tone = sec["tone"]
    (st.success if tone == "risk-on" else st.warning if tone == "risk-off" else st.info)(
        f"**{tone.upper()}** — {sec['read']}")
    try:
        import plotly.graph_objects as go
        rows = sec["rows"]
        colors = [theme.GREEN if r["pct"] >= 0 else theme.RED for r in rows]
        fig = go.Figure(go.Bar(
            x=[r["pct"] for r in rows], y=[r["label"] for r in rows],
            orientation="h", marker_color=colors))
        fig.update_layout(height=440, xaxis_title="day % change",
                          yaxis=dict(autorange="reversed"))
        st.plotly_chart(theme.style_fig(fig), use_container_width=True)
    except Exception:
        import pandas as pd
        st.dataframe(pd.DataFrame([{"Sector": r["label"], "Chg%": r["pct"]}
                                   for r in sec["rows"]]),
                     use_container_width=True, hide_index=True)
else:
    st.info("Sector data unavailable.")

st.caption("For the model-driven NQ tilt that weights these by rolling "
           "correlation, see the NQ Macro Bias page.")
