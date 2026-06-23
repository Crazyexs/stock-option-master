"""
 GEX Day-Trade Levels  —  ES / NQ / GC, realtime, zero clicks.
================================================================================
This page has NO mode selector and NO inputs. It pulls the option chains live on
load (and auto-refreshes), computes the day-trade gamma levels with the corrected
institutional formula in gex_core.py, and prints a ready-to-trade level ladder +
playbook for ES, NQ and GC.

Read gex_core.py's header for the math and the honest data caveats (CBOE free
feed is ~15-min delayed and OI updates at the close — the WALLS are slow-moving
levels, only SPOT is live).
"""

from datetime import datetime

import pandas as pd
import streamlit as st

try:
    import pytz
    _ET = pytz.timezone("America/New_York")
except Exception:
    _ET = None

import gex_core as gx

st.set_page_config(page_title="GEX Day-Trade Levels", layout="wide")
import theme
theme.apply()


# ── Cached data pull (ttl matches the ~2-min CBOE delay) ──────────────────────
@st.cache_data(ttl=120, show_spinner=False)
def _load(_stamp: str) -> dict:
    # _stamp busts the cache when the user hits Refresh.
    return gx.compute_all()


def _now_et() -> datetime:
    return datetime.now(_ET) if _ET else datetime.now()


# ── Header / controls ─────────────────────────────────────────────────────────
st.title(" GEX Day-Trade Levels")
st.caption(
    "Live gamma walls, zero-gamma flip and 1σ band for **ES / NQ / GC** — "
    "computed from CBOE SPX·NDX·GLD option gamma. Walls = where dealer hedging "
    "creates support/resistance; zero-gamma = the regime pivot (above → fade, "
    "below → trend)."
)

c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    if st.button(" Refresh data", width='stretch'):
        st.session_state["_gex_stamp"] = _now_et().strftime("%H:%M:%S")
        st.rerun()
with c2:
    auto = st.toggle("Auto-refresh 60s", value=False,
                     help="Re-pulls the chain every 60 seconds while this tab is open.")
with c3:
    st.markdown(
        f"<div style='text-align:right;color:#888'>Session: "
        f"<b>{_now_et():%Y-%m-%d %H:%M:%S %Z}</b></div>",
        unsafe_allow_html=True,
    )

stamp = st.session_state.get("_gex_stamp", "init")


# ── Rendering helpers ─────────────────────────────────────────────────────────
def _fmt(v, dash="—"):
    return f"{v:,.2f}" if isinstance(v, (int, float)) else dash


def _regime_badge(d: dict) -> str:
    flip = d.get("gamma_flip")
    spot = d.get("spot")
    net = d.get("net_gex", 0.0)
    # With a nearby flip, regime = spot vs flip. Without one, the book is
    # one-sided and regime = sign of net GEX (net>0 pin, net<0 trend).
    pos = (spot >= flip) if (flip is not None and spot is not None) else (net >= 0)
    tail = "" if flip is not None else "  (no flip near spot — one-sided book)"
    if pos:
        return " POSITIVE γ — PIN / MEAN-REVERT (fade extremes)" + tail
    return " NEGATIVE γ — TREND / MOMENTUM (trade breakouts)" + tail


def _level_ladder(d: dict) -> pd.DataFrame:
    """Sorted top→bottom price ladder with each level tagged."""
    spot = d.get("spot")
    cw_s, pw_s = d.get("call_wall_strength"), d.get("put_wall_strength")
    cw_d, pw_d = d.get("call_wall_dist_sigma"), d.get("put_wall_dist_sigma")
    cw_tag = "main resistance / sell zone"
    if isinstance(cw_s, (int, float)):
        cw_tag += f" · {cw_s:.0f}% of call γ"
        if isinstance(cw_d, (int, float)):
            cw_tag += f" · {cw_d:+.1f}σ away"
    pw_tag = "main support / buy zone"
    if isinstance(pw_s, (int, float)):
        pw_tag += f" · {pw_s:.0f}% of put γ"
        if isinstance(pw_d, (int, float)):
            pw_tag += f" · {pw_d:+.1f}σ away"
    rows = [
        ("Call Wall 2", d.get("secondary_call_wall"), "extra resistance"),
        (" CALL WALL", d.get("call_wall"), cw_tag),
        ("Upper 1σ", d.get("upper_1sigma"), "expected-move ceiling"),
        (" ZERO GAMMA", d.get("gamma_flip"), "regime pivot"),
        (" SPOT", spot, "current price"),
        ("Lower 1σ", d.get("lower_1sigma"), "expected-move floor"),
        (" PUT WALL", d.get("put_wall"), pw_tag),
        ("Put Wall 2", d.get("secondary_put_wall"), "extra support"),
    ]
    rows = [(n, v, t) for (n, v, t) in rows if isinstance(v, (int, float))]
    rows.sort(key=lambda r: r[1], reverse=True)
    return pd.DataFrame(
        [{"Level": n, "Price": f"{v:,.2f}",
          "Δ vs spot": (f"{(v-spot)/spot*100:+.2f}%" if spot else "—"),
          "Meaning": t} for (n, v, t) in rows]
    )


def _render_symbol(sym: str, d: dict):
    st.markdown(f"### {sym}")
    if d.get("error"):
        st.error(f"{sym}: {d['error']}")
        return

    spot = d.get("spot")
    top = st.columns([1, 1, 1, 1])
    top[0].metric("Spot", _fmt(spot))
    top[1].metric("Zero Gamma", _fmt(d.get("gamma_flip")))
    net = d.get("net_gex", 0.0)
    top[2].metric("Net GEX ($/1% move)",
                  f"{net/1e9:+.2f} B" if abs(net) >= 1e9 else f"{net/1e6:+.0f} M")
    exp = d.get("expiry") or "—"
    top[3].metric("Expiry used", f"{exp}" + ("  (0DTE)" if d.get("is_0dte") else f"  ({d.get('dte')}DTE)"))

    st.markdown(f"**Regime:** {_regime_badge(d)}")
    st.markdown(f"**Bias:** {d.get('bias','—')}")

    # Second-order flow row: vanna ($/vol-pt) and charm (mechanical decay drift).
    vex, cex = d.get("net_vex", 0.0), d.get("net_cex", 0.0)
    drift = d.get("charm_drift", "flat")
    flow = st.columns([1, 1, 2])
    flow[0].metric("Net VEX ($Δ/vol-pt)",
                   f"{vex/1e9:+.2f} B" if abs(vex) >= 1e9 else f"{vex/1e6:+.0f} M",
                   help="Vanna exposure: dealer $delta added per +1 vol point. "
                        ">0 → falling VIX creates a mechanical bid (vanna rally).")
    flow[1].metric("Net CEX ($Δ/day)",
                   f"{cex/1e9:+.2f} B" if abs(cex) >= 1e9 else f"{cex/1e6:+.0f} M",
                   help="Charm exposure: dealer $delta drift per day as options "
                        "decay. Drives the systematic afternoon ramp/fade.")
    arrow = "" if drift.startswith("up") else ("" if drift.startswith("down") else "")
    flow[2].markdown(
        f"**Charm drift (experimental):** {arrow} **{drift}** — mechanical "
        "0DTE-decay pressure into the close. *Confirm with the snapshot backtest "
        "before trading the direction.*")

    left, right = st.columns([3, 2])
    with left:
        st.dataframe(_level_ladder(d), hide_index=True, width='stretch')
        st.markdown("**Playbook**")
        for step in d.get("playbook", []):
            st.markdown(f"- {step}")
    with right:
        agg = d.get("agg")
        if isinstance(agg, pd.DataFrame) and not agg.empty:
            chart = agg[["strike", "net_gex"]].copy()
            chart["Net $Gamma (M)"] = chart["net_gex"] / 1e6
            chart = chart.set_index("strike")[["Net $Gamma (M)"]]
            st.caption("Net dealer $gamma by strike (green=above flip pins, red below=fuel)")
            st.bar_chart(chart, height=320)


# ── Main render (optionally inside an auto-refreshing fragment) ────────────────
def _render_all():
    try:
        results = _load(stamp if not auto else _now_et().strftime("%H:%M"))
    except Exception as exc:
        st.error(f"Failed to load GEX data: {exc}")
        return

    # A transient CBOE hiccup (common in the first seconds after the open) can
    # blank all three symbols. Don't let that failure sit in the 120s cache —
    # clear it so the next rerun / Refresh re-fetches instead of serving the
    # stale error.
    if results and all(isinstance(v, dict) and v.get("error") for v in results.values()):
        _load.clear()
        st.warning(
            "CBOE returned no usable data on that pull — usually a brief feed "
            "hiccup right after the open. The cache was cleared; click "
            "**Refresh data** (or wait a few seconds and rerun)."
        )

    # Passive time-series: append a snapshot at most once per minute so the Wall
    # Migration page builds history without the user clicking anything.
    try:
        _mk = _now_et().strftime("%Y-%m-%d %H:%M")
        if st.session_state.get("_gex_snap_min") != _mk:
            gx.snapshot_levels(results)
            st.session_state["_gex_snap_min"] = _mk
    except Exception:
        pass

    for i, sym in enumerate(("ES", "NQ", "GC")):
        _render_symbol(sym, results.get(sym, {}))
        if i < 2:
            st.divider()

    st.divider()
    sc1, sc2 = st.columns([1, 3])
    with sc1:
        if st.button(" Log snapshot", width='stretch',
                     help="Append the current levels to gex_snapshots.csv to build "
                          "the backtest dataset (score hit-rates of walls / flips later)."):
            try:
                path = gx.snapshot_levels(results)
                st.success(f"Logged to {path}")
            except Exception as exc:
                st.error(f"Snapshot failed: {exc}")
    with sc2:
        st.caption("Log snapshots through the session, then score each level "
                   "(touch-and-reject of walls, flip-cross → trend day) to learn "
                   "the hit-rate per level type per regime — the only honest test "
                   "of whether a signal is tradeable.")

    st.markdown("####  TradingView / algo levels (pipe string)")
    st.code(gx.pipe_string(results) or "no data", language="text")
    st.caption(
        " Data: CBOE free CDN — quotes ~15 min delayed, open interest updates "
        "at the close. The WALLS are slow-moving (today they reflect yesterday's "
        "close OI); only SPOT updates live. GEX shows where reactions are likely, "
        "not a profit guarantee — your stops and size still decide P&L."
    )


# st.fragment(run_every=...) gives true in-place auto-refresh on modern Streamlit.
if auto and hasattr(st, "fragment"):
    @st.fragment(run_every=60)
    def _auto():
        _render_all()
    _auto()
else:
    _render_all()
