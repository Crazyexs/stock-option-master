""" Options Flow & Skew — what is trading NOW, not just the standing OI book.

Volume put/call ratios, net premium ($), volume-weighted FLOW GEX vs the OI
wall, and 25-delta risk-reversal / butterfly skew — all from the same CBOE chain
the GEX engine downloads. Flow that fights the standing gamma wall is the early
tell that the wall fails; skew steepening leads vol expansion.
"""
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Options Flow", layout="wide")
import theme
theme.apply()

import flow_core as fc

st.title("Options Flow & Skew")
st.caption("Today's tape vs the standing book. Volume = intraday flow (leads); "
           "OI = positioning (lags, updates after close). Net premium weights by "
           "dollars, not contract counts.")

c1, c2, c3 = st.columns([2, 2, 1])
with c1:
    sym = st.text_input("Symbol (ES / NQ / GC or any CBOE ticker: SPY, QQQ, NVDA…)",
                        value="NQ").strip().upper() or "NQ"
with c2:
    rng = st.slider("Strike window (± % of spot)", 4, 25, 12) / 100.0
with c3:
    st.write("")
    if st.button("Refresh", type="primary"):
        st.cache_data.clear()


@st.cache_data(ttl=120, show_spinner="Fetching CBOE chain…")
def _load(sym, rng):
    return fc.analyze(sym, strike_range=rng)


res = _load(sym, rng)
if res.get("error"):
    st.error(f"{sym}: {res['error']}")
    st.stop()

spot = res["spot"]
st.markdown(f"**{sym}** via CBOE `{res['cboe']}` — spot `{spot:,.2f}`  ·  "
            f"{len(res['expiries'])} expiries loaded")

tab_flow, tab_gex, tab_skew = st.tabs(["Flow & Premium", "Flow GEX vs OI", "Skew"])

# ── Flow & premium ────────────────────────────────────────────────────────────
with tab_flow:
    for scope, label in (("0dte", "0DTE (today's expiry)"), ("all", "All loaded expiries")):
        f = res[f"flow_{scope}"]
        st.markdown(f"#### {label}")
        if f.get("error"):
            st.info(f"No {scope} flow: {f['error']}")
            continue
        m = st.columns(4)
        m[0].metric("Put/Call vol", f["pcr_volume"] if f["pcr_volume"] is not None else "n/a",
                    help="Today's traded put volume / call volume. >1.2 fear, <0.6 greed.")
        m[1].metric("Put/Call OI", f["pcr_oi"] if f["pcr_oi"] is not None else "n/a",
                    help="Standing book ratio. Volume PCR above OI PCR = fresh hedging.")
        m[2].metric("Net premium $", f"{f['net_premium']:,.0f}",
                    delta=f["premium_lean"],
                    help="Call premium minus put premium bought today (dollars).")
        m[3].metric("Contracts", f"{f['n_contracts']:,}")
        st.markdown(f"- Tape tone: **{f['tone']}**  ·  aggressor (hint): _{f['aggressor']}_")
        st.markdown(f"- Call vol `{f['call_volume']:,.0f}` / Put vol `{f['put_volume']:,.0f}`  ·  "
                    f"Call prem `${f['call_premium']:,.0f}` / Put prem `${f['put_premium']:,.0f}`")
        st.divider()

# ── Flow GEX vs OI GEX ────────────────────────────────────────────────────────
with tab_gex:
    fg = res["flow_vs_oi"]
    if fg.get("error"):
        st.info(f"Flow GEX unavailable: {fg['error']}")
    else:
        m = st.columns(3)
        m[0].metric("OI net GEX (standing)", f"{fg['oi_net_gex']:,.0f}",
                    help="$ dealer gamma per 1% move from open interest. + pins, − trends.")
        m[1].metric("Flow net GEX (today)", f"{fg['flow_net_gex']:,.0f}",
                    help="Same, weighted by TODAY'S volume — where new gamma is being built.")
        m[2].metric("OI call wall", f"{fg['oi_call_wall']:,.2f}" if fg['oi_call_wall'] else "n/a")
        st.success(fg["relation"]) if "REINFORCES" in fg["relation"] else st.warning(fg["relation"])
        if fg.get("flow_top_strike"):
            st.caption(f"Flow is concentrating gamma at strike {fg['flow_top_strike']:,.2f}.")

        # Overlay OI vs flow net-gamma per strike.
        oi_agg = fg["oi_agg"][["strike", "net_gex"]].rename(columns={"net_gex": "OI net GEX"})
        vol_agg = fg["vol_agg"][["strike", "net_gex"]].rename(columns={"net_gex": "Flow net GEX"})
        merged = pd.merge(oi_agg, vol_agg, on="strike", how="outer").fillna(0.0).sort_values("strike")
        try:
            import plotly.graph_objects as go
            fig = go.Figure()
            fig.add_bar(x=merged["strike"], y=merged["OI net GEX"], name="OI net GEX",
                        marker_color=theme.DIM)
            fig.add_bar(x=merged["strike"], y=merged["Flow net GEX"], name="Flow net GEX",
                        marker_color=theme.AMBER)
            fig.add_vline(x=spot, line_dash="dash", line_color=theme.GREEN)
            fig.update_layout(barmode="overlay", height=420, legend_orientation="h",
                              xaxis_title="strike", yaxis_title="$ net dealer gamma / 1%")
            st.plotly_chart(theme.style_fig(fig), use_container_width=True)
        except Exception:
            st.dataframe(merged, use_container_width=True, hide_index=True)

# ── Skew ──────────────────────────────────────────────────────────────────────
with tab_skew:
    sk = res["skew"]
    if sk.get("error"):
        st.info(f"Skew unavailable: {sk['error']}")
    else:
        st.markdown(f"#### 25-delta skew · front expiry `{sk['expiry']}`")
        m = st.columns(4)
        m[0].metric("ATM IV", f"{sk['atm_iv']:.2f}" if sk['atm_iv'] else "n/a")
        m[1].metric("25d Risk-Reversal", f"{sk['rr25']:+.2f}",
                    help="IV(25d call) − IV(25d put). Negative = put skew (fear).")
        m[2].metric("25d Butterfly", f"{sk['bf25']:+.2f}" if sk['bf25'] is not None else "n/a",
                    help="Wing IV minus ATM IV — smile curvature / tail bid.")
        m[3].metric("25d put IV", f"{sk['iv_25d_put']:.2f}")
        st.markdown(f"- Read: **{sk['skew_read']}**")
        st.caption(f"25d call strike {sk['strike_25d_call']:,.2f} (IV {sk['iv_25d_call']:.2f}) · "
                   f"25d put strike {sk['strike_25d_put']:,.2f} (IV {sk['iv_25d_put']:.2f})")

st.caption("Data: CBOE free CDN (~15-min delayed; volume is cumulative for the "
           "session, not tick data). Aggressor tilt is a weak last-tick proxy.")
