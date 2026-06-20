"""Fundamental Analysis — type a ticker, get DCF / multiples / scores / financials."""
import pandas as pd
import streamlit as st

import fundamentals_core as fc
import i18n
import theme

try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
except Exception:
    _HAS_PLOTLY = False

st.set_page_config(page_title="Fundamental Analysis", layout="wide")
theme.apply()

top = st.columns([3, 1])
with top[1]:
    i18n.language_selector()
T = i18n.t

st.title(T("fundamental_title"))

ticker = st.text_input(T("enter_ticker"), value="NVDA").strip().upper()
disc_pct = st.slider(T("discount_rate") + " (WACC override, %)", 5.0, 20.0, 9.7, 0.1)
go_btn = st.button(T("analyze"), type="primary")


@st.cache_data(ttl=900, show_spinner="Loading fundamentals…")
def _load(sym, disc):
    return fc.analyze(sym, discount_override=disc / 100.0)


if not ticker:
    st.info(T("enter_ticker"))
    st.stop()

res = _load(ticker, disc_pct)
if res.get("error"):
    st.error(res["error"])
    st.stop()

cur = res.get("currency", "USD")


def money(v, dp=2):
    return f"{v:,.{dp}f} {cur}" if isinstance(v, (int, float)) else "—"


def big(v):
    if not isinstance(v, (int, float)):
        return "—"
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v/div:,.2f} {suf} {cur}"
    return f"{v:,.0f} {cur}"


def pct(v, dp=1):
    return f"{v*100:,.{dp}f}%" if isinstance(v, (int, float)) else "—"


# ── Header / quote ────────────────────────────────────────────────────────────
st.header(f"{res['name']}  ·  {res['symbol']}")
st.caption(f"{res.get('exchange','')}  ·  {res.get('sector','')} / {res.get('industry','')}")
q = st.columns(6)
chg = res.get("change"); chgp = res.get("change_pct")
arrow = "+" if (chg or 0) >= 0 else ""
q[0].metric(T("price"), money(res.get("price")),
            f"{arrow}{chg:,.2f} ({arrow}{chgp:,.2f}%)" if chg is not None else None)
q[1].metric(T("open"), money(res.get("open")))
q[2].metric(T("high"), money(res.get("high")))
q[3].metric(T("low"), money(res.get("low")))
q[4].metric(T("volume"), f"{res.get('volume'):,.0f}" if res.get("volume") else "—")
q[5].metric(T("market_cap"), big(res.get("market_cap")))

tabs = st.tabs([T("summary"), T("dcf_valuation"), T("relative_valuation"),
                T("wallst_estimates"), T("profitability"), T("solvency"),
                T("financials"), T("dividends"), T("discount_rate")])

# ── Summary ───────────────────────────────────────────────────────────────────
with tabs[0]:
    st.subheader(T("about_company"))
    summary = res.get("summary") or ""
    if i18n.get_lang() == "TH" and summary:
        summary = i18n.translate_text(summary, "th")
    st.write(summary or "—")
    c = st.columns(3)
    c[0].metric(T("employees"), f"{res.get('employees'):,.0f}" if res.get("employees") else "—")
    c[1].metric("FCF", big(res.get("fcf")))
    c[2].metric("Net debt", big(res.get("net_debt")))

# ── DCF ───────────────────────────────────────────────────────────────────────
with tabs[1]:
    st.subheader(T("intrinsic_value"))
    price = res.get("price")
    d = res.get("dcf", {})
    cols = st.columns(3)
    for col, key in zip(cols, ("bear", "base", "bull")):
        sc = d.get(key)
        with col:
            st.markdown(f"**{T(key)}**")
            if not sc:
                st.write("—")
                continue
            ps = sc["per_share"]
            st.metric(T("dcf_value_share"), money(ps))
            if price:
                diff = (ps - price) / price * 100
                lbl = T("above_market") if diff >= 0 else T("below_market")
                st.caption(f"{abs(diff):,.0f}% {lbl}")
    base = d.get("base")
    if base and _HAS_PLOTLY and price:
        fig = go.Figure(go.Bar(
            x=[T("bear"), T("base"), T("bull"), T("price")],
            y=[d.get("bear", {}).get("per_share") if d.get("bear") else None,
               base["per_share"],
               d.get("bull", {}).get("per_share") if d.get("bull") else None, price],
            marker_color=["#EA3943", "#3B82F6", "#16C784", "#8A94A6"]))
        fig.update_layout(height=340, paper_bgcolor="#000", plot_bgcolor="#050505",
                          font=dict(color="#FFB000", family="Consolas, monospace"),
                          title=T("dcf_value_share"))
        st.plotly_chart(fig, use_container_width=True)
    if base:
        st.markdown("**" + T("summary") + "**")
        b = st.columns(2)
        b[0].metric(T("pv_forecast"), big(base["pv_forecast"]))
        b[0].metric(T("equity_value"), big(base["equity_value"]))
        b[1].metric(T("pv_terminal"), big(base["pv_terminal"]))
        b[1].metric(T("shares_out"), big(base["shares"]))
    st.caption(T("not_advice"))

# ── Relative valuation ────────────────────────────────────────────────────────
with tabs[2]:
    st.subheader(T("relative_valuation"))
    m = res.get("multiples", {})
    rows = [{"Multiple": k, "Value": (f"{v:,.2f}" if isinstance(v, (int, float)) else "—")}
            for k, v in m.items()]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')

# ── Wall St ───────────────────────────────────────────────────────────────────
with tabs[3]:
    st.subheader(T("wallst_estimates"))
    tg = res.get("targets", {})
    price = res.get("price")
    c = st.columns(3)

    def tgt(col, key, label):
        v = tg.get(key)
        col.metric(label, money(v))
        if v and price:
            diff = (v - price) / price * 100
            col.caption(f"{'+' if diff>=0 else ''}{diff:,.0f}% vs price")
    tgt(c[0], "low", T("low_target"))
    tgt(c[1], "mean", T("mean_target"))
    tgt(c[2], "high", T("high_target"))
    if tg.get("n"):
        st.caption(f"n = {tg['n']:,.0f} analysts  ·  consensus: {tg.get('reco','—')}")
    if _HAS_PLOTLY and tg.get("low") and tg.get("high") and price:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[tg["low"], tg["high"]], y=[0, 0], mode="lines",
                                 line=dict(color="#3B82F6", width=6), name="range"))
        for v, n, color in ((tg["low"], T("low_target"), "#EA3943"),
                            (tg["mean"], T("mean_target"), "#3B82F6"),
                            (tg["high"], T("high_target"), "#16C784"),
                            (price, T("price"), "#FFB000")):
            if v:
                fig.add_trace(go.Scatter(x=[v], y=[0], mode="markers+text", text=[n],
                                         textposition="top center", marker=dict(size=12, color=color),
                                         showlegend=False))
        fig.update_layout(height=240, paper_bgcolor="#000", plot_bgcolor="#050505",
                          font=dict(color="#FFB000", family="Consolas, monospace"),
                          yaxis=dict(visible=False), title=T("analyst_targets"))
        st.plotly_chart(fig, use_container_width=True)

# ── Profitability ─────────────────────────────────────────────────────────────
with tabs[4]:
    st.subheader(T("profitability"))
    p = res.get("profitability", {})
    c = st.columns(3)
    c[0].metric(T("gross_margin"), pct(p.get("gross_margin")))
    c[1].metric(T("operating_margin"), pct(p.get("operating_margin")))
    c[2].metric(T("net_margin"), pct(p.get("net_margin")))
    c2 = st.columns(4)
    c2[0].metric("ROE", pct(p.get("roe")))
    c2[1].metric("ROA", pct(p.get("roa")))
    c2[2].metric("ROIC", pct(p.get("roic")))
    c2[3].metric("ROCE", pct(p.get("roce")))

# ── Solvency ──────────────────────────────────────────────────────────────────
with tabs[5]:
    st.subheader(T("solvency"))
    z = res.get("altman")
    if z:
        c = st.columns(2)
        c[0].metric(T("altman_z"), f"{z['z']:,.2f}")
        c[1].metric(T("bankruptcy"), z["zone"])
        comp = pd.DataFrame([
            {"Component": "X1 liquidity (WC/TA)", "Value": f"{z['x1']:.3f}"},
            {"Component": "X2 retained earnings/TA", "Value": f"{z['x2']:.3f}"},
            {"Component": "X3 EBIT/TA", "Value": f"{z['x3']:.3f}"},
            {"Component": "X4 mkt equity/liabilities", "Value": f"{z['x4']:.3f}"},
            {"Component": "X5 sales/TA", "Value": f"{z['x5']:.3f}"},
        ])
        st.dataframe(comp, hide_index=True, width='stretch')
        st.caption("Z > 2.99 Safe · 1.81-2.99 Grey · < 1.81 Distress (manufacturing model).")
    else:
        st.info("Solvency data unavailable for this ticker.")

# ── Financials ────────────────────────────────────────────────────────────────
with tabs[6]:
    st.subheader(T("financials"))
    inc = res.get("income_stmt")
    if _HAS_PLOTLY and inc is not None:
        rev = fc._row(inc, "Total Revenue", "Operating Revenue")
        ni = fc._row(inc, "Net Income", "Net Income Common Stockholders")
        if rev is not None:
            yrs = [str(c)[:10] for c in rev.index][::-1]
            fig = go.Figure()
            fig.add_bar(x=yrs, y=[rev.get(c) for c in rev.index][::-1],
                        name="Revenue", marker_color="#3B82F6")
            if ni is not None:
                fig.add_bar(x=yrs, y=[ni.get(c) for c in ni.index][::-1],
                            name="Net income", marker_color="#16C784")
            fig.update_layout(height=360, barmode="group", paper_bgcolor="#000",
                              plot_bgcolor="#050505", font=dict(color="#FFB000",
                              family="Consolas, monospace"), title=T("revenue_income"))
            st.plotly_chart(fig, use_container_width=True)
    for label, df in ((T("income_statement"), res.get("income_stmt")),
                      (T("balance_sheet"), res.get("balance_sheet")),
                      (T("cash_flow"), res.get("cashflow"))):
        with st.expander(label):
            if df is not None and not df.empty:
                show = (df / 1e6).round(1)
                show.columns = [str(c)[:10] for c in show.columns]
                st.dataframe(show, width='stretch')
                st.caption("Values in millions.")
            else:
                st.info("Not available.")

# ── Dividends ─────────────────────────────────────────────────────────────────
with tabs[7]:
    st.subheader(T("dividends"))
    dv = res.get("dividends", {})
    c = st.columns(3)
    c[0].metric(T("dividend_yield"), pct(dv.get("yield"), 2))
    c[1].metric(T("dividend_rate"), money(dv.get("rate")))
    c[2].metric(T("payout_ratio"), pct(dv.get("payout")))

# ── Discount rate ─────────────────────────────────────────────────────────────
with tabs[8]:
    st.subheader(T("discount_rate"))
    cp = res.get("capm", {})
    c = st.columns(2)
    c[0].metric(T("cost_of_equity"), pct(cp.get("coe"), 2))
    c[1].metric(T("wacc"), pct(cp.get("wacc"), 2))
    detail = pd.DataFrame([
        {"Input": T("risk_free"), "Value": pct(cp.get("risk_free"), 2)},
        {"Input": T("beta"), "Value": f"{cp.get('beta'):.2f}" if cp.get("beta") else "—"},
        {"Input": "ERP", "Value": pct(cp.get("erp"), 2)},
        {"Input": "Cost of debt", "Value": pct(cp.get("cost_of_debt"), 2)},
        {"Input": "Tax rate", "Value": pct(cp.get("tax"), 1)},
        {"Input": "Equity weight", "Value": pct(cp.get("equity_weight"), 1)},
        {"Input": "Debt weight", "Value": pct(cp.get("debt_weight"), 1)},
    ])
    st.dataframe(detail, hide_index=True, width='stretch')
    st.caption("CoE = risk-free + beta × ERP.  WACC = E/(E+D)·CoE + D/(E+D)·Kd·(1−tax).")

st.caption(T("not_advice"))
