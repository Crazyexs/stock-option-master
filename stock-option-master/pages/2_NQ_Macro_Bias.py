"""
 NQ Macro Bias — cross-asset + FOMC/NFP + GEX, fused into one directional read.
================================================================================
Pulls the cross-asset universe (ES, VIX, DXY, 10Y, gold, oil, BTC, semis), measures
each one's correlation to NQ and its latest standardised move, sums them into an
NQ tilt → implied P(up), then overlays the NQ dealer-gamma regime and the US event
calendar to tell you HOW to trade it. See macro_core.py for the math + caveats.
"""

import pandas as pd
import streamlit as st

import macro_core as mc

st.set_page_config(page_title="NQ Macro Bias", layout="wide")
import theme
theme.apply()


@st.cache_data(ttl=300, show_spinner="Pulling cross-asset data…")
def _load(_stamp: str) -> dict:
    return mc.compute_nq_bias()


st.title(" NQ Macro Bias")
st.caption(
    "One directional read for **NQ** from everything that moves it: cross-asset "
    "correlations, the US event calendar (FOMC/NFP), and NQ's own gamma regime. "
    "P(up) is a heuristic composite — confirm at GEX levels, not a standalone signal."
)

c1, c2 = st.columns([1, 4])
with c1:
    if st.button(" Refresh", width='stretch'):
        st.session_state["_nq_stamp"] = pd.Timestamp.utcnow().strftime("%H:%M:%S")
        st.rerun()
stamp = st.session_state.get("_nq_stamp", "init")

res = _load(stamp)
for err in res.get("errors", []):
    st.warning(err)

# ── Event banner ──────────────────────────────────────────────────────────────
ev = res.get("event", {})
if ev.get("event"):
    flag = ev["flag"]
    msg = f"**Next US event:** {ev['event']} — {ev['date']} ({ev['days']}d away)"
    if flag in ("TODAY", "tomorrow"):
        st.error(" " + msg + f"  ·  **{flag.upper()}: gamma pins break, size down / wait.**")
    elif flag == "this week":
        st.warning(" " + msg + "  ·  this week — expect vol expansion.")
    else:
        st.info(" " + msg)
    up = ev.get("upcoming", [])
    if up:
        st.caption("Upcoming: " + " · ".join(f"{d} {n}" for d, n in up))

# ── Headline bias ─────────────────────────────────────────────────────────────
p_up = res.get("p_up", 50.0)
bias = res.get("bias", "NEUTRAL")
color = {"UP": "", "DOWN": "", "NEUTRAL": ""}.get(bias, "")

m = st.columns([1, 1, 1, 1])
m[0].metric("NQ bias", f"{color} {bias}")
m[1].metric("P(up) implied", f"{p_up:.0f}%")
m[2].metric("Confidence", f"{res.get('confidence','—')}",
            help="Damped near FOMC/NFP and in pin regime (where intraday follow-through is weak).")
m[3].metric("NQ last move", f"{res.get('nq_move_pct','—')}%")

st.progress(min(max(p_up / 100.0, 0.0), 1.0),
            text=f"P(down) {100-p_up:.0f}%  ←→  P(up) {p_up:.0f}%")

st.markdown(f"**GEX regime:** {res.get('regime','—')}  ·  **Cross-asset tilt:** {res.get('tilt','—')}")

st.markdown("####  How to trade it")
for step in res.get("play", []):
    st.markdown(f"- {step}")

st.divider()

# ── Cross-asset contributions ─────────────────────────────────────────────────
left, right = st.columns([3, 2])
with left:
    st.markdown("#### Cross-asset contributions to the NQ tilt")
    st.caption("contribution = corr(asset, NQ) × standardised latest move. "
               "Positive ⇒ pulls NQ up, negative ⇒ pulls NQ down.")
    comps = res.get("components", [])
    if comps:
        df = pd.DataFrame(comps)
        df.columns = ["Asset", "Corr vs NQ", "Move %", "z-move", "Contribution"]
        df = df.sort_values("Contribution", ascending=False)
        st.dataframe(df, hide_index=True, width='stretch')
    else:
        st.info("No cross-asset components available.")

with right:
    st.markdown("#### NQ GEX snapshot")
    gex = res.get("gex", {})
    if isinstance(gex, dict) and not gex.get("error"):
        g = st.columns(2)
        g[0].metric("Spot", f"{gex.get('spot'):,.0f}" if gex.get("spot") else "—")
        g[1].metric("Zero γ", f"{gex.get('gamma_flip'):,.0f}" if gex.get("gamma_flip") else "—")
        g2 = st.columns(2)
        g2[0].metric("Call wall", f"{gex.get('call_wall'):,.0f}" if gex.get("call_wall") else "—")
        g2[1].metric("Put wall", f"{gex.get('put_wall'):,.0f}" if gex.get("put_wall") else "—")
        st.caption("Full ladder + playbook on the  GEX Day-Trade page.")
    else:
        st.info(f"NQ GEX unavailable: {gex.get('error','—') if isinstance(gex, dict) else '—'}")

# ── Correlation matrix ────────────────────────────────────────────────────────
st.markdown("#### Correlation matrix (60-day daily returns)")
corr = res.get("corr")
if isinstance(corr, pd.DataFrame) and not corr.empty:
    try:
        st.dataframe(corr.style.background_gradient(cmap="RdYlGn", axis=None,
                                                    vmin=-1, vmax=1).format("{:.2f}"),
                     width='stretch')
    except Exception:
        st.dataframe(corr, width='stretch')
else:
    st.info("Correlation matrix unavailable.")

# ── Snapshot for backtest ─────────────────────────────────────────────────────
st.divider()
sc1, sc2 = st.columns([1, 3])
with sc1:
    if st.button(" Log NQ bias", width='stretch'):
        try:
            path = mc.snapshot_nq_bias(res)
            st.success(f"Logged to {path}")
        except Exception as exc:
            st.error(f"Snapshot failed: {exc}")
with sc2:
    st.caption("Log the bias through the session, then score whether NQ actually "
               "closed in the predicted direction by regime / event state — that's "
               "the only way to learn if P(up) is calibrated and worth sizing against.")

st.caption(
    " P(up) is a heuristic composite, not a calibrated probability (clamped 15–85%). "
    "Cross-asset moves are daily/close-to-close. FOMC dates are static — verify vs "
    "federalreserve.gov. Confirm direction with price action at GEX levels."
)
