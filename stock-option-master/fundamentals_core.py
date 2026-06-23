"""
fundamentals_core.py — equity fundamental analysis from a ticker.
================================================================================
Pure logic (no Streamlit). Given a symbol it returns a flat dict with:
  - quote / company overview
  - DCF intrinsic value (bear / base / bull)
  - relative valuation multiples
  - Wall St analyst targets
  - profitability (margins, ROE/ROA/ROIC/ROCE)
  - solvency (Altman Z-score + ratios)
  - financial statements (income / balance / cashflow)
  - dividends
  - cost of equity (CAPM) + WACC

Data source: yfinance (Yahoo). Yahoo data is delayed/estimated and NOT licensed
for commercial resale — for a paid product swap this for a licensed vendor (see
the SaaS ARCHITECTURE doc). Everything is best-effort: missing fields return None
and the UI shows a dash. None of this is investment advice.
"""

from __future__ import annotations

import math


# ── Safe helpers ──────────────────────────────────────────────────────────────
def _f(v):
    try:
        if v is None:
            return None
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def _row(df, *names):
    """First matching row (most-recent-first Series) from a yfinance statement."""
    if df is None or getattr(df, "empty", True):
        return None
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if not s.empty:
                return s
    return None


def _latest(df, *names):
    s = _row(df, *names)
    return _f(s.iloc[0]) if s is not None and len(s) else None


# ── Risk-free rate ────────────────────────────────────────────────────────────
def _risk_free():
    try:
        import yf_session as yfs
        h = yfs.make_ticker("^TNX").history(period="5d")["Close"].dropna()
        if not h.empty:
            return float(h.iloc[-1]) / 100.0
    except Exception:
        pass
    return 0.044


# ── DCF ───────────────────────────────────────────────────────────────────────
def dcf(fcf, growth, discount, years=5, term_growth=0.025, net_debt=0.0, shares=None):
    if not fcf or fcf <= 0 or not shares or shares <= 0 or discount <= term_growth:
        return None
    pv_fc, f = 0.0, float(fcf)
    for yr in range(1, years + 1):
        f *= (1 + growth)
        pv_fc += f / ((1 + discount) ** yr)
    terminal = f * (1 + term_growth) / (discount - term_growth)
    pv_term = terminal / ((1 + discount) ** years)
    ev = pv_fc + pv_term
    equity = ev - net_debt
    return {
        "per_share": equity / shares,
        "pv_forecast": pv_fc,
        "pv_terminal": pv_term,
        "equity_value": equity,
        "shares": shares,
        "growth": growth,
        "discount": discount,
    }


# ── Altman Z-score ────────────────────────────────────────────────────────────
def altman_z(bs, is_, mcap):
    ta = _latest(bs, "Total Assets")
    if not ta:
        return None
    ca = _latest(bs, "Current Assets", "Total Current Assets")
    cl = _latest(bs, "Current Liabilities", "Total Current Liabilities")
    re = _latest(bs, "Retained Earnings")
    ebit = _latest(is_, "EBIT", "Operating Income")
    sales = _latest(is_, "Total Revenue", "Operating Revenue")
    tl = _latest(bs, "Total Liabilities Net Minority Interest", "Total Liab", "Total Liabilities")
    if None in (ca, cl, tl) or not tl:
        return None
    x1 = (ca - cl) / ta
    x2 = (re or 0) / ta
    x3 = (ebit or 0) / ta
    x4 = (mcap or 0) / tl
    x5 = (sales or 0) / ta
    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    zone = "Safe" if z > 2.99 else ("Grey" if z >= 1.81 else "Distress")
    return {"z": z, "zone": zone, "x1": x1, "x2": x2, "x3": x3, "x4": x4, "x5": x5}


# ── Main ──────────────────────────────────────────────────────────────────────
def analyze(symbol: str, erp: float = 0.045, discount_override: float | None = None,
            base_growth_override: float | None = None) -> dict:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return {"error": "Enter a ticker symbol."}
    try:
        import yf_session as yfs
        import pandas as pd  # noqa: F401  (ensures pandas present for statements)
        t = yfs.make_ticker(symbol)
        try:
            info = t.info or {}
        except Exception:
            info = {}
        if not info or not (info.get("longName") or info.get("shortName") or info.get("regularMarketPrice")):
            return {"error": f"No data for '{symbol}'. Check the ticker."}

        price = _f(info.get("currentPrice")) or _f(info.get("regularMarketPrice"))
        prev = _f(info.get("previousClose"))
        change = (price - prev) if (price and prev) else None
        change_pct = (change / prev * 100.0) if (change is not None and prev) else None

        income = _safe_stmt(t, "income_stmt")
        balance = _safe_stmt(t, "balance_sheet")
        cash = _safe_stmt(t, "cashflow")

        shares = _f(info.get("sharesOutstanding"))
        mcap = _f(info.get("marketCap"))
        total_debt = _f(info.get("totalDebt")) or 0.0
        total_cash = _f(info.get("totalCash")) or 0.0
        net_debt = total_debt - total_cash
        beta = _f(info.get("beta")) or 1.0

        # Free cash flow (info first, else a "Free Cash Flow" row, else OCF - CapEx).
        fcf = _f(info.get("freeCashflow"))
        if not fcf:
            fcf = _latest(cash, "Free Cash Flow")
        if not fcf:
            ocf = _latest(cash, "Operating Cash Flow", "Total Cash From Operating Activities",
                          "Cash Flow From Continuing Operating Activities")
            capex = _latest(cash, "Capital Expenditure", "Capital Expenditures",
                            "Purchase Of PPE")
            if ocf is not None and capex is not None:
                fcf = ocf - abs(capex)

        # Cost of equity (CAPM) + WACC.
        rf = _risk_free()
        coe = rf + beta * erp
        int_exp = _latest(income, "Interest Expense")
        kd = (abs(int_exp) / total_debt) if (int_exp and total_debt) else 0.05
        tax = _f(info.get("effectiveTaxRate"))
        if tax is None:
            pretax = _latest(income, "Pretax Income", "Income Before Tax")
            taxp = _latest(income, "Tax Provision", "Income Tax Expense")
            tax = (taxp / pretax) if (pretax and taxp) else 0.18
        tax = min(max(tax, 0.0), 0.40)
        e = mcap or 0.0
        d = total_debt
        wacc = coe if (e + d) <= 0 else (e / (e + d) * coe + d / (e + d) * kd * (1 - tax))

        # DCF scenarios.
        base_g = base_growth_override
        if base_g is None:
            base_g = _f(info.get("earningsGrowth")) or _f(info.get("revenueGrowth")) or 0.08
        base_g = min(max(base_g, -0.05), 0.30)        # sane bounds
        disc = discount_override or wacc
        scen = {
            "bear": dcf(fcf, base_g * 0.5, disc + 0.02, net_debt=net_debt, shares=shares),
            "base": dcf(fcf, base_g, disc, net_debt=net_debt, shares=shares),
            "bull": dcf(fcf, min(base_g * 1.35, 0.35), max(disc - 0.01, 0.04),
                        net_debt=net_debt, shares=shares),
        }

        # Relative valuation multiples.
        multiples = {
            "P/E": _f(info.get("trailingPE")),
            "Forward P/E": _f(info.get("forwardPE")),
            "P/S": _f(info.get("priceToSalesTrailing12Months")),
            "P/B": _f(info.get("priceToBook")),
            "EV/EBITDA": _f(info.get("enterpriseToEbitda")),
            "EV/Revenue": _f(info.get("enterpriseToRevenue")),
            "PEG": _f(info.get("pegRatio")),
        }

        # Profitability.
        prof = {
            "gross_margin": _f(info.get("grossMargins")),
            "operating_margin": _f(info.get("operatingMargins")),
            "net_margin": _f(info.get("profitMargins")),
            "roe": _f(info.get("returnOnEquity")),
            "roa": _f(info.get("returnOnAssets")),
        }
        ebit = _latest(income, "EBIT", "Operating Income")
        ta = _latest(balance, "Total Assets")
        cl = _latest(balance, "Current Liabilities", "Total Current Liabilities")
        equity_bv = _latest(balance, "Stockholders Equity", "Total Stockholder Equity")
        invested_capital = total_debt + (equity_bv or 0.0)
        prof["roic"] = (ebit * (1 - tax) / invested_capital) if (ebit and invested_capital) else None
        prof["roce"] = (ebit / (ta - cl)) if (ebit and ta and cl is not None and (ta - cl)) else None

        z = altman_z(balance, income, mcap)

        # Analyst targets.
        targets = {
            "low": _f(info.get("targetLowPrice")),
            "mean": _f(info.get("targetMeanPrice")),
            "high": _f(info.get("targetHighPrice")),
            "n": _f(info.get("numberOfAnalystOpinions")),
            "reco": info.get("recommendationKey"),
        }

        dividends = {
            "yield": _f(info.get("dividendYield")),
            "rate": _f(info.get("dividendRate")),
            "payout": _f(info.get("payoutRatio")),
        }

        return {
            "symbol": symbol,
            "name": info.get("longName") or info.get("shortName") or symbol,
            "exchange": info.get("fullExchangeName") or info.get("exchange"),
            "currency": info.get("currency", "USD"),
            "price": price, "change": change, "change_pct": change_pct,
            "open": _f(info.get("open")), "high": _f(info.get("dayHigh")),
            "low": _f(info.get("dayLow")), "volume": _f(info.get("volume")),
            "market_cap": mcap, "employees": _f(info.get("fullTimeEmployees")),
            "sector": info.get("sector"), "industry": info.get("industry"),
            "summary": info.get("longBusinessSummary"),
            "shares": shares, "fcf": fcf, "net_debt": net_debt,
            "dcf": scen, "multiples": multiples, "targets": targets,
            "profitability": prof, "altman": z, "dividends": dividends,
            "capm": {"risk_free": rf, "beta": beta, "erp": erp, "coe": coe,
                     "wacc": wacc, "cost_of_debt": kd, "tax": tax,
                     "equity_weight": (e / (e + d) if (e + d) else 1.0),
                     "debt_weight": (d / (e + d) if (e + d) else 0.0)},
            "income_stmt": income, "balance_sheet": balance, "cashflow": cash,
            "error": None,
        }
    except Exception as exc:
        return {"error": f"{symbol}: {exc}"}


def _safe_stmt(ticker, attr):
    try:
        df = getattr(ticker, attr)
        return df if df is not None and not df.empty else None
    except Exception:
        return None


# ── Verdict / scoring ─────────────────────────────────────────────────────────
# Turns the raw numbers into the three plain-English reads a user actually wants:
#   1. Is it cheap or expensive? (valuation vs DCF + analyst targets)
#   2. Are the financials good? (margins, returns, solvency, leverage, cash)
#   3. How is it performing? (revenue / earnings growth)
# Each is a transparent, weighted heuristic — NOT advice. Thresholds are coarse
# and sector-agnostic, so treat the labels as a starting point, not a signal.
def _pct(a, b):
    return ((a - b) / b * 100.0) if (a is not None and b not in (None, 0)) else None


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _sub(v, good, ok, higher_better=True):
    """Map a metric to a 0/0.6/1.0 sub-score against two thresholds."""
    if v is None:
        return None
    if higher_better:
        return 1.0 if v >= good else (0.6 if v >= ok else 0.2)
    return 1.0 if v <= good else (0.6 if v <= ok else 0.2)


def verdict(res: dict) -> dict:
    """Plain-English valuation / health / performance read from an analyze() dict."""
    if not res or res.get("error"):
        return {}
    price = res.get("price")
    base = (res.get("dcf") or {}).get("base") or {}
    tg = res.get("targets") or {}
    prof = res.get("profitability") or {}
    mult = res.get("multiples") or {}
    capm = res.get("capm") or {}
    z = res.get("altman") or {}
    fcf = res.get("fcf")
    mcap = res.get("market_cap")
    net_debt = res.get("net_debt")
    income = res.get("income_stmt")

    strengths: list[str] = []
    risks: list[str] = []

    # ── 1. Valuation ──
    up_dcf = _pct(base.get("per_share"), price)
    up_an = _pct(tg.get("mean"), price)
    ups = [u for u in (up_dcf, up_an) if u is not None]
    val_up = sum(ups) / len(ups) if ups else None
    if val_up is None:
        val_label, val_color = "n/a", "warn"
    elif val_up >= 20:
        val_label, val_color = "Undervalued", "good"
    elif val_up <= -10:
        val_label, val_color = "Overvalued", "bad"
    else:
        val_label, val_color = "Fairly valued", "warn"
    if up_dcf is not None:
        (strengths if up_dcf > 0 else risks).append(
            f"DCF base value is {abs(up_dcf):.0f}% {'above' if up_dcf > 0 else 'below'} the current price")
    if up_an is not None:
        (strengths if up_an > 0 else risks).append(
            f"Analyst mean target is {abs(up_an):.0f}% {'above' if up_an > 0 else 'below'} the price")
    peg = mult.get("PEG")
    if peg is not None:
        if peg < 1:
            strengths.append(f"PEG {peg:.2f} — cheap relative to its growth")
        elif peg > 2.5:
            risks.append(f"PEG {peg:.2f} — expensive relative to its growth")
    pe = mult.get("P/E")
    if pe is not None and pe > 40:
        risks.append(f"High P/E {pe:.0f} — priced for strong growth")

    # ── 2. Financial health ── (weight, sub-score)
    subs: list[tuple[float, float]] = []
    nm = prof.get("net_margin")
    s = _sub(nm, 0.15, 0.05)
    if s is not None:
        subs.append((1.0, s))
        if nm >= 0.15:
            strengths.append(f"Fat net margin {nm * 100:.0f}%")
        elif nm < 0:
            risks.append("Unprofitable — negative net margin")
    roe = prof.get("roe")
    s = _sub(roe, 0.15, 0.08)
    if s is not None:
        subs.append((1.0, s))
        if roe >= 0.15:
            strengths.append(f"Strong ROE {roe * 100:.0f}%")
        elif roe < 0:
            risks.append("Negative ROE")
    roic, wacc = prof.get("roic"), capm.get("wacc")
    if roic is not None and wacc:
        if roic > wacc + 0.02:
            subs.append((1.2, 1.0))
            strengths.append(f"ROIC {roic * 100:.0f}% beats WACC {wacc * 100:.0f}% — creating value")
        elif roic > wacc:
            subs.append((1.2, 0.6))
        else:
            subs.append((1.2, 0.2))
            risks.append(f"ROIC {roic * 100:.0f}% below WACC {wacc * 100:.0f}% — destroying value")
    if z.get("zone"):
        subs.append((1.0, {"Safe": 1.0, "Grey": 0.6, "Distress": 0.1}.get(z["zone"], 0.5)))
        if z["zone"] == "Safe":
            strengths.append(f"Altman Z {z['z']:.1f} — bankruptcy-safe")
        elif z["zone"] == "Distress":
            risks.append(f"Altman Z {z['z']:.1f} — financial-distress zone")
    if fcf is not None:
        if fcf > 0:
            subs.append((1.0, 1.0))
            strengths.append("Generates positive free cash flow")
        else:
            subs.append((1.0, 0.2))
            risks.append("Burning cash — negative free cash flow")
    if net_debt is not None and mcap:
        lev = net_debt / mcap
        if lev <= 0:
            subs.append((0.8, 1.0))
            strengths.append("Net-cash balance sheet (more cash than debt)")
        elif lev <= 0.3:
            subs.append((0.8, 0.6))
        else:
            subs.append((0.8, 0.2))
            risks.append(f"High leverage — net debt is {lev * 100:.0f}% of market cap")

    # ── 3. Performance (growth from statements) ──
    rev = _row(income, "Total Revenue", "Operating Revenue")
    ni = _row(income, "Net Income", "Net Income Common Stockholders")
    rev_g = ni_g = None
    if rev is not None and len(rev) >= 2 and rev.iloc[1]:
        rev_g = rev.iloc[0] / rev.iloc[1] - 1
        s = _sub(rev_g, 0.12, 0.02)
        if s is not None:
            subs.append((1.0, s))
        (strengths if rev_g >= 0.02 else risks).append(
            f"Revenue {'grew' if rev_g >= 0 else 'fell'} {abs(rev_g) * 100:.0f}% year-over-year")
    if ni is not None and len(ni) >= 2 and ni.iloc[1] and ni.iloc[1] > 0:
        ni_g = ni.iloc[0] / ni.iloc[1] - 1
        (strengths if ni_g >= 0 else risks).append(
            f"Net income {'grew' if ni_g >= 0 else 'fell'} {abs(ni_g) * 100:.0f}% year-over-year")

    health_score = (sum(w * sc for w, sc in subs) / sum(w for w, _ in subs)) if subs else None
    if health_score is None:
        health_label, health_color = "n/a", "warn"
    elif health_score >= 0.7:
        health_label, health_color = "Strong", "good"
    elif health_score >= 0.45:
        health_label, health_color = "Adequate", "warn"
    else:
        health_label, health_color = "Weak", "bad"

    # ── Overall blend (valuation + health + analyst stance) ──
    reco = (tg.get("reco") or "").lower()
    reco_score = {"strong_buy": 1.0, "buy": 0.8, "outperform": 0.8, "hold": 0.5,
                  "neutral": 0.5, "underperform": 0.25, "sell": 0.1,
                  "strong_sell": 0.0}.get(reco)
    val_score = _clamp(0.5 + val_up / 100.0) if val_up is not None else None
    parts = []
    if val_score is not None:
        parts.append((2.0, val_score))
    if health_score is not None:
        parts.append((2.0, health_score))
    if reco_score is not None:
        parts.append((1.0, reco_score))
    overall = (sum(w * s for w, s in parts) / sum(w for w, _ in parts)) if parts else 0.5
    score100 = int(round(overall * 100))
    if score100 >= 70:
        o_label, o_color = "Attractive", "good"
    elif score100 >= 45:
        o_label, o_color = "Mixed / fair", "warn"
    else:
        o_label, o_color = "Caution", "bad"

    name = res.get("name", res.get("symbol"))
    summary = (f"**{name}** looks **{o_label.lower()}**. Valuation: **{val_label.lower()}**"
               + (f" ({val_up:+.0f}% to estimated fair value)" if val_up is not None else "")
               + f". Financial health: **{health_label.lower()}**"
               + (f" ({int(health_score * 100)}/100)" if health_score is not None else "")
               + "."
               + (f" Analysts lean **{reco.replace('_', ' ')}**." if reco else ""))

    return {
        "overall": {"label": o_label, "color": o_color, "score": score100, "summary": summary},
        "valuation": {"label": val_label, "color": val_color, "upside": val_up,
                      "up_dcf": up_dcf, "up_analyst": up_an},
        "health": {"label": health_label, "color": health_color,
                   "score": int(health_score * 100) if health_score is not None else None},
        "performance": {"rev_growth": rev_g, "ni_growth": ni_g},
        "strengths": strengths, "risks": risks, "reco": reco,
    }
