""" Contract Chart — underlying candles with GEX walls / flip overlaid."""
import pandas as pd
import streamlit as st

import gex_core as gx

try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
except Exception:
    _HAS_PLOTLY = False

st.set_page_config(page_title="Contract Chart", layout="wide")
import theme
theme.apply()
st.title(" Contract Chart")
st.caption("Price candles with the live GEX levels drawn on — see how price is "
           "reacting to the walls and zero-gamma flip in real time.")

_YF = {"ES": "ES=F", "NQ": "NQ=F", "GC": "GC=F"}

c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
sym = c1.selectbox("Symbol", ["ES", "NQ", "GC"])
period = c2.selectbox("Period", ["5d", "1mo", "3mo", "6mo"], index=0)
interval = c3.selectbox("Interval", ["5m", "15m", "30m", "1h", "1d"], index=2)
if c4.button(" Refresh", width='stretch'):
    st.session_state["cc_stamp"] = pd.Timestamp.utcnow().strftime("%H%M%S")
stamp = st.session_state.get("cc_stamp", "init")


@st.cache_data(ttl=180, show_spinner="Loading price + GEX…")
def _load(sym, period, interval, stamp):
    import yfinance as yf
    try:
        hist = yf.Ticker(_YF[sym]).history(period=period, interval=interval)
    except Exception as exc:
        hist = None
    levels = gx.compute_symbol(sym)
    return hist, levels


hist, lv = _load(sym, period, interval, stamp)
if hist is None or hist.empty:
    st.error("No price data from yfinance for this period/interval combo.")
    st.stop()

if _HAS_PLOTLY:
    fig = go.Figure(go.Candlestick(
        x=hist.index, open=hist["Open"], high=hist["High"],
        low=hist["Low"], close=hist["Close"], name=sym))
    if not lv.get("error"):
        overlays = [
            ("Call Wall", lv.get("call_wall"), "#d62728"),
            ("Zero Gamma", lv.get("gamma_flip"), "#1f77b4"),
            ("Put Wall", lv.get("put_wall"), "#2ca02c"),
            ("Upper 1σ", lv.get("upper_1sigma"), "#888"),
            ("Lower 1σ", lv.get("lower_1sigma"), "#888"),
        ]
        for name, val, color in overlays:
            if isinstance(val, (int, float)):
                fig.add_hline(y=val, line_color=color, line_dash="dot",
                              annotation_text=name, annotation_position="right")
    fig.update_layout(height=640, xaxis_rangeslider_visible=False,
                      paper_bgcolor="#000000", plot_bgcolor="#050505",
                      font=dict(color="#FFB000", family="Consolas, monospace"),
                      title=f"{sym} ({_YF[sym]}) — {interval} candles + GEX levels")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.line_chart(hist["Close"])

if not lv.get("error"):
    m = st.columns(4)
    m[0].metric("Spot", f"{lv.get('spot'):,.2f}" if lv.get("spot") else "—")
    m[1].metric("Call Wall", f"{lv.get('call_wall'):,.0f}" if lv.get("call_wall") else "—")
    m[2].metric("Zero Gamma", f"{lv.get('gamma_flip'):,.0f}" if lv.get("gamma_flip") else "—")
    m[3].metric("Put Wall", f"{lv.get('put_wall'):,.0f}" if lv.get("put_wall") else "—")
    st.markdown(f"**Regime:** {lv.get('regime','—')} · **Bias:** {lv.get('bias','—')}")
st.caption(" Futures price is live; GEX walls reflect last-close OI (delayed feed).")
