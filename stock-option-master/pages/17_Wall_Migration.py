""" Wall Migration — how the GEX levels move through the session.

Every snapshot logged on the GEX Day-Trade page (auto, once a minute) is replayed
here as a time series. The single most tradable GEX signal is MIGRATION: a call
wall drifting UP through the day is bullish (dealers chasing price, pin rising);
walls collapsing toward spot warns of a pin break. A static snapshot can't show
this — the history can.
"""
from datetime import datetime
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Wall Migration", layout="wide")
import theme
theme.apply()

import gex_core as gx

st.title("Wall Migration — GEX Levels Over Time")
st.caption("Replays gex_snapshots.csv (written by the GEX Day-Trade page). Leave "
           "that page open with auto-refresh to accumulate intraday history.")

SNAP = "gex_snapshots.csv"


@st.cache_data(ttl=60)
def _load(_stamp):
    try:
        df = pd.read_csv(SNAP)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    return df.dropna(subset=["timestamp"]).sort_values("timestamp")


c1, c2, c3 = st.columns([1, 1, 1])
with c1:
    if st.button("Refresh", type="primary"):
        st.cache_data.clear()

df = _load(datetime.now().strftime("%H:%M"))
if df.empty:
    st.info("No snapshots yet. Open the **GEX Day-Trade Levels** page (it logs a "
            "snapshot once a minute), or click **Log snapshot** there, then come "
            "back. You can also let it auto-refresh to build a session history.")
    st.stop()

with c2:
    syms = sorted(df["symbol"].dropna().unique())
    sym = st.selectbox("Symbol", syms, index=syms.index("NQ") if "NQ" in syms else 0)
with c3:
    sub = df[df["symbol"] == sym].copy()
    n_pts = len(sub)
    st.metric("Snapshots", n_pts)

if sub.empty or n_pts < 2:
    st.warning(f"Only {n_pts} snapshot(s) for {sym}. Need at least 2 to draw "
               "migration — let the day-trade page run a while.")
    st.stop()

# ── Migration chart: spot + walls + flip over time ────────────────────────────
LEVELS = [
    ("spot", theme.OFFWHITE, "Spot"),
    ("call_wall", theme.RED, "Call Wall"),
    ("put_wall", theme.GREEN, "Put Wall"),
    ("gamma_flip", theme.AMBER, "Zero Gamma"),
]
try:
    import plotly.graph_objects as go
    fig = go.Figure()
    for col, color, name in LEVELS:
        if col in sub.columns and sub[col].notna().any():
            dash = "dash" if col == "gamma_flip" else None
            fig.add_scatter(x=sub["timestamp"], y=pd.to_numeric(sub[col], errors="coerce"),
                            mode="lines+markers", name=name,
                            line=dict(color=color, width=2, dash=dash),
                            marker=dict(size=5))
    fig.update_layout(height=460, legend_orientation="h",
                      xaxis_title="time", yaxis_title="price",
                      title=f"{sym} — wall & flip migration")
    st.plotly_chart(theme.style_fig(fig), use_container_width=True)
except Exception:
    st.line_chart(sub.set_index("timestamp")[["spot", "call_wall", "put_wall", "gamma_flip"]])

# ── Migration read (first vs last) ────────────────────────────────────────────
first, last = sub.iloc[0], sub.iloc[-1]


def _drift(col):
    try:
        a, b = float(first[col]), float(last[col])
        return b - a
    except Exception:
        return None


cw_d, pw_d, fl_d = _drift("call_wall"), _drift("put_wall"), _drift("gamma_flip")
m = st.columns(4)
m[0].metric("Call wall drift", f"{cw_d:+.2f}" if cw_d is not None else "n/a",
            help="Up = bullish pin migration; down = ceiling falling toward spot.")
m[1].metric("Put wall drift", f"{pw_d:+.2f}" if pw_d is not None else "n/a",
            help="Up = floor rising (support firming under price).")
m[2].metric("Zero-gamma drift", f"{fl_d:+.2f}" if fl_d is not None else "n/a")
try:
    net_first, net_last = float(first.get("net_gex", 0)), float(last.get("net_gex", 0))
    m[3].metric("Net GEX", f"{net_last:,.0f}", f"{net_last - net_first:+,.0f}")
except Exception:
    pass

notes = []
if cw_d is not None and cw_d > 0:
    notes.append("Call wall migrating UP — bullish; dealers chasing, pin rising.")
elif cw_d is not None and cw_d < 0:
    notes.append("Call wall migrating DOWN — ceiling compressing toward spot; pin-break risk.")
if pw_d is not None and pw_d < 0:
    notes.append("Put wall migrating DOWN — support giving way; downside opening up.")
if fl_d is not None and abs(fl_d) > 0:
    notes.append(f"Zero-gamma {'rising' if fl_d > 0 else 'falling'} — regime pivot is "
                 f"{'moving up with price' if fl_d > 0 else 'dropping (trend risk builds)'}.")
if notes:
    st.markdown("\n".join(f"- {n}" for n in notes))

with st.expander("Raw snapshots"):
    show = ["timestamp", "spot", "call_wall", "put_wall", "gamma_flip", "net_gex",
            "regime", "bias"]
    st.dataframe(sub[[c for c in show if c in sub.columns]],
                 use_container_width=True, hide_index=True)
