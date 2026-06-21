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


def sidebar_language_selector():
    """
    Compact EN/TH translate toggle rendered in the sidebar on EVERY page (called
    from theme.apply). Flips st.session_state['lang'] and reruns; the auto-translate
    layer below then machine-translates the whole page on the fly when TH is active.
    """
    try:
        current = get_lang()
        with st.sidebar:
            choice = st.radio("LANGUAGE / ภาษา", ["EN", "TH"],
                              index=0 if current == "EN" else 1,
                              horizontal=True, key="i18n_lang_global",
                              help="Translate every page to Thai (machine translation, "
                                   "best-effort). Switch back to EN any time.")
        if choice != current:
            set_lang(choice)
            st.rerun()
    except Exception:
        pass


# ── Whole-page auto-translation (install once; self-gates per session) ─────────
# When the user picks TH, we machine-translate the text of every Streamlit widget
# on the fly so we never have to hand-translate 14 pages. We wrap the relevant
# DeltaGenerator methods (so column / sidebar / container calls like
# `col.metric(...)` are covered too — not just module-level `st.*`). The wrapper
# checks the language at CALL time, so it is safe when several users with
# different languages share one Streamlit server process.

import threading as _threading
_guard = _threading.local()

# Methods whose first string arg (and selected text kwargs) is user-facing copy.
_WRAP_NAMES = (
    "title", "header", "subheader", "markdown", "caption", "text", "write",
    "info", "warning", "error", "success", "metric", "button", "download_button",
    "checkbox", "toggle", "radio", "selectbox", "multiselect", "select_slider",
    "slider", "text_input", "number_input", "text_area", "expander", "tabs",
    "page_link", "progress",
)
_TXT_KWARGS = ("body", "label", "text", "help", "caption", "title")
# Interactive widgets: translating the label can collide two English labels into
# one Thai string → Streamlit DuplicateWidgetID. For keyless ones we inject a key
# derived from the ORIGINAL English label (already unique, since the app runs in EN).
_WIDGET_NAMES = {"button", "download_button", "checkbox", "toggle", "radio",
                 "selectbox", "multiselect", "select_slider", "slider",
                 "text_input", "number_input", "text_area"}


def _xlate(s: str) -> str:
    if not isinstance(s, str) or not s.strip():
        return s
    if s.lstrip().startswith("<"):          # raw HTML — never translate
        return s
    try:
        return translate_text(s, "th")
    except Exception:
        return s


def _apply_translation(args: tuple, kwargs: dict, fname: str, base: int):
    """`base` skips the leading `self` when wrapping DeltaGenerator class methods."""
    if kwargs.get("unsafe_allow_html"):     # CSS / HTML blocks (tape, tables)
        return args, kwargs
    args = list(args)
    # Capture the ORIGINAL English label (positional first, else kwarg) before we
    # translate it — used to key keyless interactive widgets.
    orig_label = None
    if fname not in ("page_link", "tabs"):
        if len(args) > base and isinstance(args[base], str):
            orig_label = args[base]
        elif isinstance(kwargs.get("label"), str):
            orig_label = kwargs["label"]
    if fname in _WIDGET_NAMES and orig_label and "key" not in kwargs:
        kwargs["key"] = "i18n_%s_%s" % (fname, orig_label)

    if fname == "tabs" and len(args) > base and isinstance(args[base], (list, tuple)):
        args[base] = [_xlate(x) if isinstance(x, str) else x for x in args[base]]
    elif fname == "page_link":
        pass                                # first positional is a path/target, never text
    elif len(args) > base and isinstance(args[base], str):
        args[base] = _xlate(args[base])
    for k in _TXT_KWARGS:
        if k in kwargs and isinstance(kwargs[k], str):
            kwargs[k] = _xlate(kwargs[k])
    return tuple(args), kwargs


def _wrap(func, fname: str, self_arg: bool):
    if getattr(func, "_i18n_wrapped", False):
        return func
    base = 1 if self_arg else 0     # class methods receive `self` as args[0]

    def inner(*args, **kwargs):
        # English → passthrough. Re-entrant Streamlit calls → translate once only.
        if get_lang() != "TH" or getattr(_guard, "busy", False):
            return func(*args, **kwargs)
        _guard.busy = True
        try:
            try:
                args, kwargs = _apply_translation(args, kwargs, fname, base)
            except Exception:
                pass
            return func(*args, **kwargs)
        finally:
            _guard.busy = False

    inner._i18n_wrapped = True
    return inner


def install_autotranslate():
    """Idempotently wrap Streamlit's text/widget methods for on-the-fly TH output."""
    if getattr(st, "_i18n_installed", False):
        return
    # Wrap the DeltaGenerator class so column/sidebar/container calls (col.metric,
    # st.sidebar.markdown, …) are covered — these pass `self` as the first arg.
    try:
        from streamlit.delta_generator import DeltaGenerator
        for name in _WRAP_NAMES:
            f = getattr(DeltaGenerator, name, None)
            if callable(f):
                setattr(DeltaGenerator, name, _wrap(f, name, self_arg=True))
    except Exception:
        pass
    # Wrap the module-level st.* aliases too (already bound — no `self` in args).
    for name in _WRAP_NAMES:
        g = getattr(st, name, None)
        if callable(g) and not getattr(g, "_i18n_wrapped", False):
            try:
                setattr(st, name, _wrap(g, name, self_arg=False))
            except Exception:
                pass
    st._i18n_installed = True


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
