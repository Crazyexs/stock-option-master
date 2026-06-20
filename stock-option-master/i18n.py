"""
i18n.py — lightweight English/Thai toggle for the app.
================================================================================
Usage in a page:
    import i18n
    i18n.language_selector()          # renders the EN/TH switch
    st.title(i18n.t("fundamental_title"))
    desc_th = i18n.translate_text(long_english_text)   # machine-translate blocks

Static UI labels come from the dictionary below (fast, reliable). Long dynamic
text (e.g. a company description from Yahoo, in English) is machine-translated on
demand via deep-translator when the language is Thai. Translation is best-effort:
if deep-translator is missing or the network fails, the original text is shown.

To translate another page, wrap its label strings in i18n.t("key") and add the
key here. The language is stored in st.session_state so it persists across pages.
"""

import streamlit as st

_STRINGS = {
    "language":            {"EN": "Language",            "TH": "ภาษา"},
    "fundamental_title":   {"EN": "Fundamental Analysis", "TH": "การวิเคราะห์ปัจจัยพื้นฐาน"},
    "enter_ticker":        {"EN": "Enter a stock ticker (e.g. NVDA, AAPL, PTT.BK)",
                            "TH": "พิมพ์สัญลักษณ์หุ้น (เช่น NVDA, AAPL, PTT.BK)"},
    "analyze":             {"EN": "Analyze",             "TH": "วิเคราะห์"},
    "summary":             {"EN": "Summary",             "TH": "สรุป"},
    "dcf_valuation":       {"EN": "DCF Valuation",       "TH": "มูลค่าตาม DCF"},
    "relative_valuation":  {"EN": "Relative Valuation",  "TH": "มูลค่าเชิงเปรียบเทียบ"},
    "wallst_estimates":    {"EN": "Wall St Estimates",   "TH": "ประมาณการนักวิเคราะห์"},
    "profitability":       {"EN": "Profitability",       "TH": "ความสามารถทำกำไร"},
    "solvency":            {"EN": "Solvency",            "TH": "ความมั่นคงทางการเงิน"},
    "financials":          {"EN": "Financials",          "TH": "งบการเงิน"},
    "dividends":           {"EN": "Dividends",           "TH": "เงินปันผล"},
    "discount_rate":       {"EN": "Discount Rate",       "TH": "อัตราคิดลด"},
    "price":               {"EN": "Price",               "TH": "ราคา"},
    "open":                {"EN": "Open",                "TH": "ราคาเปิด"},
    "high":                {"EN": "High",                "TH": "สูงสุด"},
    "low":                 {"EN": "Low",                 "TH": "ต่ำสุด"},
    "volume":              {"EN": "Volume",              "TH": "ปริมาณ"},
    "market_cap":          {"EN": "Market cap",          "TH": "มูลค่าตลาด"},
    "employees":           {"EN": "Employees",           "TH": "พนักงาน"},
    "about_company":       {"EN": "About the company",   "TH": "เกี่ยวกับบริษัท"},
    "dcf_value_share":     {"EN": "DCF value per share", "TH": "มูลค่า DCF ต่อหุ้น"},
    "intrinsic_value":     {"EN": "DCF Intrinsic Value", "TH": "มูลค่าที่แท้จริงตาม DCF"},
    "bear":                {"EN": "Bear case",           "TH": "กรณีแย่"},
    "base":                {"EN": "Base case",           "TH": "กรณีฐาน"},
    "bull":                {"EN": "Bull case",           "TH": "กรณีดี"},
    "above_market":        {"EN": "above market price",  "TH": "สูงกว่าราคาตลาด"},
    "below_market":        {"EN": "below market price",  "TH": "ต่ำกว่าราคาตลาด"},
    "pv_forecast":         {"EN": "PV of forecast cash flows", "TH": "มูลค่าปัจจุบันของกระแสเงินสดที่คาดการณ์"},
    "pv_terminal":         {"EN": "PV of terminal value", "TH": "มูลค่าปัจจุบันของมูลค่าสุดท้าย"},
    "equity_value":        {"EN": "Equity value",        "TH": "มูลค่าส่วนของผู้ถือหุ้น"},
    "shares_out":          {"EN": "Shares outstanding",  "TH": "จำนวนหุ้น"},
    "analyst_targets":     {"EN": "Analyst price targets (12-month)", "TH": "ราคาเป้าหมายนักวิเคราะห์ (12 เดือน)"},
    "low_target":          {"EN": "Low target",          "TH": "เป้าหมายต่ำสุด"},
    "mean_target":         {"EN": "Average target",      "TH": "เป้าหมายเฉลี่ย"},
    "high_target":         {"EN": "High target",         "TH": "เป้าหมายสูงสุด"},
    "gross_margin":        {"EN": "Gross margin",        "TH": "อัตรากำไรขั้นต้น"},
    "operating_margin":    {"EN": "Operating margin",    "TH": "อัตรากำไรจากการดำเนินงาน"},
    "net_margin":          {"EN": "Net margin",          "TH": "อัตรากำไรสุทธิ"},
    "altman_z":            {"EN": "Altman Z-Score",      "TH": "คะแนน Altman Z"},
    "bankruptcy":          {"EN": "Bankruptcy risk zone", "TH": "ระดับความเสี่ยงล้มละลาย"},
    "income_statement":    {"EN": "Income statement",    "TH": "งบกำไรขาดทุน"},
    "balance_sheet":       {"EN": "Balance sheet",       "TH": "งบดุล"},
    "cash_flow":           {"EN": "Cash flow statement", "TH": "งบกระแสเงินสด"},
    "dividend_yield":      {"EN": "Dividend yield",      "TH": "อัตราผลตอบแทนเงินปันผล"},
    "dividend_rate":       {"EN": "Dividend per share",  "TH": "เงินปันผลต่อหุ้น"},
    "payout_ratio":        {"EN": "Payout ratio",        "TH": "อัตราการจ่ายปันผล"},
    "cost_of_equity":      {"EN": "Cost of equity (CAPM)", "TH": "ต้นทุนส่วนของผู้ถือหุ้น (CAPM)"},
    "wacc":                {"EN": "WACC",                "TH": "ต้นทุนเงินทุนถัวเฉลี่ย (WACC)"},
    "risk_free":           {"EN": "Risk-free rate",      "TH": "อัตราปลอดความเสี่ยง"},
    "beta":                {"EN": "Beta",                "TH": "ค่าเบต้า"},
    "not_advice":          {"EN": "Not investment advice. Yahoo data is delayed/estimated.",
                            "TH": "ไม่ใช่คำแนะนำการลงทุน ข้อมูล Yahoo อาจล่าช้า/เป็นการประมาณ"},
    "revenue_income":      {"EN": "Revenue & net income (annual)", "TH": "รายได้และกำไรสุทธิ (รายปี)"},
}


def get_lang() -> str:
    return st.session_state.get("lang", "EN")


def set_lang(lang: str):
    st.session_state["lang"] = lang


def t(key: str) -> str:
    d = _STRINGS.get(key)
    if not d:
        return key
    return d.get(get_lang(), d.get("EN", key))


def language_selector(label: str | None = None):
    """Renders the EN/TH switch and stores the choice."""
    current = get_lang()
    choice = st.radio(label or t("language"), ["EN", "TH"],
                      index=0 if current == "EN" else 1,
                      horizontal=True, key="_lang_radio")
    if choice != current:
        set_lang(choice)
        st.rerun()


@st.cache_data(ttl=86400, show_spinner=False)
def _translate_cached(text: str, target: str) -> str:
    try:
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source="auto", target=target).translate(text[:4900])
    except Exception:
        return text


def translate_text(text: str | None, target: str | None = None) -> str:
    """Machine-translate a block of dynamic text to the active language."""
    if not text:
        return text or ""
    tgt = target or ("th" if get_lang() == "TH" else "en")
    return _translate_cached(text, tgt)
