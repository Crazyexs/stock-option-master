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
        import yfinance as yf
        h = yf.Ticker("^TNX").history(period="5d")["Close"].dropna()
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
        import yfinance as yf
        import pandas as pd  # noqa: F401  (ensures pandas present for statements)
        t = yf.Ticker(symbol)
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

        # Free cash flow (info first, else OCF - CapEx).
        fcf = _f(info.get("freeCashflow"))
        if not fcf:
            ocf = _latest(cash, "Operating Cash Flow", "Total Cash From Operating Activities")
            capex = _latest(cash, "Capital Expenditure", "Capital Expenditures")
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
        prof["roic"] = (ebit * (1 - tax) / (total_debt + equity_bv)) if (ebit and (total_debt + (equity_bv or 0))) else None
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
