"""
chain_core.py — full option-chain engine + reusable Plotly figures.
================================================================================
One place that fetches the WHOLE CBOE chain (every strike & expiry) for ES/NQ/GC
with all first- and second-order Greeks, builds per-strike / per-expiry exposure
profiles (DEX, GEX, VEX, Vega, Charm), and returns ready-to-render Plotly figures
(bars, IV heatmap, 3-D IV surface, 3-D exposure surface, OI). No Streamlit here —
pages import this and call st.plotly_chart, so the math/plots stay reusable.

EXPOSURE SIGN CONVENTION (same retail-flow assumption as gex_core):
    dealer is LONG calls (+1), SHORT puts (−1)  ⇒  pos_sign = +1 call / −1 put
    exposure_i = pos_sign · greek_i · OI_i · multiplier(S)
Greeks that are sign-symmetric across call/put (gamma, vega, vanna) get their
direction purely from pos_sign; delta/charm already carry a type sign, and
pos_sign then encodes the dealer's long-call/short-put book. This is an
*assumption* (well-validated for SPX/NDX, see gex_core header), not measured flow.
GC is shown in GLD terms (≈ gold/10); multiply strikes ×10 for the GC equivalent.
"""

from __future__ import annotations

import math
from datetime import date as _date, datetime as _datetime

import pandas as pd

import gex_core as gx

try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
except Exception:                                    # pragma: no cover
    _HAS_PLOTLY = False

_CM = 100.0
_MIN_T = 1.0 / 24.0 / 365.0

SYMBOLS = {"ES": ("SPX", True), "NQ": ("NDX", True), "GC": ("GLD", False)}


# ── Exposure definitions ──────────────────────────────────────────────────────
# key -> (greek column, $multiplier(S), label, unit caption)
GREEK_SPECS = {
    "DEX":   ("delta", lambda S: _CM * S,            "Delta Exposure (DEX)",
              "$ dealer delta"),
    "GEX":   ("gamma", lambda S: _CM * S * S * 0.01, "Gamma Exposure (GEX)",
              "$ hedge per 1% move"),
    "VEX":   ("vanna", lambda S: _CM * S,            "Vanna Exposure (VEX)",
              "$ delta per 1 vol-pt"),
    "VEGA":  ("vega",  lambda S: _CM,                "Vega Exposure",
              "$ value per 1 vol-pt"),
    "CHARM": ("charm", lambda S: _CM * S,            "Charm Exposure (CEX)",
              "$ delta drift per day"),
}


# ── Spot ──────────────────────────────────────────────────────────────────────
def get_spot(sym: str) -> tuple[float, dict]:
    """Native CBOE spot (SPX/NDX/GLD) + the raw payload."""
    cboe_sym, is_index = SYMBOLS[sym]
    raw = gx.fetch_cboe_raw(cboe_sym, is_index)
    data = raw.get("data", {})
    spot = float(data.get("current_price") or data.get("close") or 0)
    return spot, data


# ── Full chain ────────────────────────────────────────────────────────────────
def load_chain(sym: str, strike_range: float = 0.15, max_dte: int = 120) -> dict:
    """
    Parse the entire CBOE chain into a tidy frame with every Greek.
    Returns {"df", "spot", "symbol", "cboe", "error"}.
    """
    try:
        spot, data = get_spot(sym)
        if not spot:
            return {"error": f"{sym}: no spot from CBOE", "df": pd.DataFrame(), "spot": 0}
        opts = data.get("options", [])
        if not opts:
            return {"error": f"{sym}: no options from CBOE", "df": pd.DataFrame(), "spot": spot}
        q = gx._DIV_YIELD.get(sym, 0.0)
        today = _date.today()
        today_str = today.isoformat()
        rows = []
        for rec in opts:
            code = rec.get("option", "")
            try:
                i = next(j for j, c in enumerate(code) if c.isdigit())
                exp_str = f"20{code[i:i+2]}-{code[i+2:i+4]}-{code[i+4:i+6]}"
                otype = code[i + 6]
                K = float(code[i + 7:]) / 1000.0
            except Exception:
                continue
            if spot <= 0 or abs(K - spot) / spot > strike_range:
                continue
            try:
                exp_date = _datetime.strptime(exp_str, "%Y-%m-%d").date()
            except Exception:
                continue
            dte = (exp_date - today).days
            if dte < 0 or dte > max_dte:
                continue
            oi = float(rec.get("open_interest") or 0)
            vol = float(rec.get("volume") or 0)
            iv = float(rec.get("iv") or 0)
            if oi == 0 and vol == 0:
                continue
            T = max(dte / 365.0, _MIN_T)
            iv_dec = iv / 100.0 if iv > 1.0 else iv
            delta = _num(rec.get("delta"))
            gamma = _num(rec.get("gamma"))
            vega = _num(rec.get("vega"))
            theta = _num(rec.get("theta"))
            # Fill gamma + compute vanna/charm from BS where we have an IV.
            if iv_dec > 0:
                g2, vanna, charm = gx._greeks_for_exposure(spot, K, T, iv_dec, otype, q=q)
                if gamma == 0:
                    gamma = g2
            else:
                vanna = charm = 0.0
            rows.append({
                "strike": round(K, 2), "type": otype, "exp": exp_str, "dte": dte,
                "oi": oi, "volume": vol, "iv": iv_dec * 100.0,
                "delta": delta, "gamma": gamma, "vega": vega, "theta": theta,
                "vanna": vanna, "charm": charm, "is_0dte": exp_str == today_str,
            })
        df = pd.DataFrame(rows)
        return {"df": df, "spot": spot, "symbol": sym,
                "cboe": SYMBOLS[sym][0], "error": None if not df.empty else f"{sym}: no strikes"}
    except Exception as exc:
        return {"error": str(exc), "df": pd.DataFrame(), "spot": 0}


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def expiries(df: pd.DataFrame) -> list[str]:
    return sorted(df["exp"].unique()) if df is not None and not df.empty else []


def filter_exp(df: pd.DataFrame, exp: str | None) -> pd.DataFrame:
    if exp in (None, "All", "") or df is None or df.empty:
        return df
    return df[df["exp"] == exp]


# ── Exposure aggregation ──────────────────────────────────────────────────────
def aggregate_greek(df: pd.DataFrame, spot: float, greek: str) -> pd.DataFrame:
    """Per-strike call / put / net dollar exposure for the chosen greek."""
    if df is None or df.empty or greek not in GREEK_SPECS:
        return pd.DataFrame()
    col, mult_fn, _, _ = GREEK_SPECS[greek]
    mult = mult_fn(spot)
    w = df.copy()
    w["pos_sign"] = w["type"].map({"C": 1.0, "P": -1.0})
    w["expo"] = w["pos_sign"] * w[col] * w["oi"] * mult
    w["call_e"] = w["expo"].where(w["type"] == "C", 0.0)
    w["put_e"] = w["expo"].where(w["type"] == "P", 0.0)
    agg = (w.groupby("strike")
           .agg(call=("call_e", "sum"), put=("put_e", "sum"), net=("expo", "sum"))
           .reset_index().sort_values("strike").reset_index(drop=True))
    agg["cum"] = agg["net"].cumsum()
    return agg


def greek_total(df: pd.DataFrame, spot: float, greek: str) -> float:
    agg = aggregate_greek(df, spot, greek)
    return float(agg["net"].sum()) if not agg.empty else 0.0


# ── Plotly figures ────────────────────────────────────────────────────────────
def _scale(v: float) -> tuple[float, str]:
    a = abs(v)
    if a >= 1e9:
        return 1e9, "B"
    if a >= 1e6:
        return 1e6, "M"
    if a >= 1e3:
        return 1e3, "K"
    return 1.0, ""


def fig_exposure_bar(agg: pd.DataFrame, spot: float, title: str, unit: str):
    if not _HAS_PLOTLY or agg is None or agg.empty:
        return None
    div, suf = _scale(agg["net"].abs().max() or 1.0)
    y = agg["net"] / div
    colors = ["#2ca02c" if v >= 0 else "#d62728" for v in agg["net"]]
    fig = go.Figure(go.Bar(x=agg["strike"], y=y, marker_color=colors,
                           hovertemplate="K %{x}<br>%{y:.2f}" + suf + "<extra></extra>"))
    fig.add_vline(x=spot, line_dash="dash", line_color="#1f77b4",
                  annotation_text=f"spot {spot:,.0f}", annotation_position="top")
    fig.update_layout(title=f"{title} — net by strike ({unit}, {suf})",
                      xaxis_title="Strike", yaxis_title=f"Net ({suf})",
                      height=420, bargap=0.05, paper_bgcolor="#000000", plot_bgcolor="#050505",
                      font=dict(color="#FFB000", family="Consolas, monospace"))
    return fig


def fig_exposure_surface(df: pd.DataFrame, spot: float, greek: str):
    """3-D surface: strike × DTE × net exposure (the dealer 'gravity field')."""
    if not _HAS_PLOTLY or df is None or df.empty or greek not in GREEK_SPECS:
        return None
    col, mult_fn, title, unit = GREEK_SPECS[greek]
    mult = mult_fn(spot)
    w = df.copy()
    w["pos_sign"] = w["type"].map({"C": 1.0, "P": -1.0})
    w["expo"] = w["pos_sign"] * w[col] * w["oi"] * mult
    piv = w.pivot_table(index="dte", columns="strike", values="expo",
                        aggfunc="sum").fillna(0.0).sort_index()
    if piv.empty:
        return None
    div, suf = _scale(piv.abs().values.max() or 1.0)
    fig = go.Figure(go.Surface(z=piv.values / div, x=piv.columns, y=piv.index,
                               colorscale="RdYlGn", cmid=0,
                               colorbar=dict(title=suf)))
    fig.update_layout(title=f"{title} surface ({unit}, {suf})",
                      scene=dict(xaxis_title="Strike", yaxis_title="DTE",
                                 zaxis_title=f"Net ({suf})"),
                      height=620, paper_bgcolor="#000000", plot_bgcolor="#050505",
                      font=dict(color="#FFB000", family="Consolas, monospace"))
    return fig


def fig_iv_heatmap(df: pd.DataFrame):
    if not _HAS_PLOTLY or df is None or df.empty:
        return None
    piv = df.pivot_table(index="dte", columns="strike", values="iv",
                         aggfunc="mean").sort_index()
    if piv.empty:
        return None
    fig = go.Figure(go.Heatmap(z=piv.values, x=piv.columns, y=piv.index,
                               colorscale="Inferno", colorbar=dict(title="IV %")))
    fig.update_layout(title="Implied-vol heatmap (skew × term structure)",
                      xaxis_title="Strike", yaxis_title="DTE",
                      height=480, paper_bgcolor="#000000", plot_bgcolor="#050505",
                      font=dict(color="#FFB000", family="Consolas, monospace"))
    return fig


def fig_iv_surface(df: pd.DataFrame):
    if not _HAS_PLOTLY or df is None or df.empty:
        return None
    piv = df.pivot_table(index="dte", columns="strike", values="iv",
                         aggfunc="mean").sort_index()
    if piv.empty:
        return None
    fig = go.Figure(go.Surface(z=piv.values, x=piv.columns, y=piv.index,
                               colorscale="Viridis", colorbar=dict(title="IV %")))
    fig.update_layout(title="Implied-vol surface (3-D)",
                      scene=dict(xaxis_title="Strike", yaxis_title="DTE",
                                 zaxis_title="IV %"),
                      height=640, paper_bgcolor="#000000", plot_bgcolor="#050505",
                      font=dict(color="#FFB000", family="Consolas, monospace"))
    return fig


def fig_oi(df: pd.DataFrame, spot: float, value: str = "oi"):
    """Calls-up / puts-down bars of OI (or volume) by strike."""
    if not _HAS_PLOTLY or df is None or df.empty:
        return None
    calls = df[df["type"] == "C"].groupby("strike")[value].sum()
    puts = df[df["type"] == "P"].groupby("strike")[value].sum()
    strikes = sorted(set(calls.index) | set(puts.index))
    cy = [calls.get(k, 0) for k in strikes]
    py = [-puts.get(k, 0) for k in strikes]
    fig = go.Figure()
    fig.add_bar(x=strikes, y=cy, name=f"Call {value}", marker_color="#2ca02c")
    fig.add_bar(x=strikes, y=py, name=f"Put {value}", marker_color="#d62728")
    fig.add_vline(x=spot, line_dash="dash", line_color="#1f77b4",
                  annotation_text=f"spot {spot:,.0f}")
    fig.update_layout(title=f"{value.upper()} by strike (calls up / puts down)",
                      barmode="relative", xaxis_title="Strike",
                      yaxis_title=value.upper(), height=460, paper_bgcolor="#000000", plot_bgcolor="#050505",
                      font=dict(color="#FFB000", family="Consolas, monospace"))
    return fig
