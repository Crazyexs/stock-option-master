""" Macro News — US event calendar (FOMC/NFP) + impact-rated live squawk.

Sources: FinancialJuice + Walter Bloomberg (@DeItaone) real-time headlines, each
rated HIGH / MEDIUM / LOW market impact, with a HIGH-impact alert banner and an
impact / source filter. Yahoo per-ticker news is kept as a tertiary feed.
"""
from datetime import date
import streamlit as st

import macro_core as mc
import news_core as nc

st.set_page_config(page_title="Macro News", layout="wide")
import theme
theme.apply()
st.title(" Macro News & Event Calendar")
st.caption("Scheduled events that break gamma pins + impact-rated live squawk. "
           "Near a red event or a HIGH-impact headline: vol expands, walls fail — "
           "size down or wait for the post-event regime.")

# ── Event calendar ────────────────────────────────────────────────────────────
st.markdown("####  Upcoming high-impact US events")
ev = mc.event_risk()
if ev.get("event"):
    flag = ev["flag"]
    head = f"**Next:** {ev['event']} — {ev['date']} ({ev['days']}d away)"
    if flag in ("TODAY", "tomorrow"):
        st.error(" " + head + f" · **{flag.upper()}** — gamma pins break, reduce size.")
    elif flag == "this week":
        st.warning(" " + head + " · this week — expect vol expansion.")
    else:
        st.info(" " + head)
    for d, name in ev.get("upcoming", []):
        days = (mc._parse(d) - date.today()).days
        st.markdown(f"- **{d}** · {name} · {days}d")
st.caption("FOMC dates are static — verify vs federalreserve.gov; CPI not auto-listed.")

st.divider()

# ── Live impact-rated squawk ────────────────────────────────────────────────────
st.markdown("####  Live squawk — impact-rated")

c1, c2, c3, c4 = st.columns([2, 1.4, 1.2, 1])
with c1:
    sources = st.multiselect("Sources", list(nc.SOURCES), default=list(nc.SOURCES))
with c2:
    impact_label = st.selectbox("Min impact",
                                ["All (LOW+)", "MEDIUM+", "HIGH only"], index=0)
    min_impact = {"All (LOW+)": "LOW", "MEDIUM+": "MEDIUM", "HIGH only": "HIGH"}[impact_label]
with c3:
    yf_ticker = st.text_input("Add Yahoo ticker", value="").strip().upper() or None
with c4:
    st.write("")
    if st.button(" Refresh", type="primary"):
        st.cache_data.clear()


@st.cache_data(ttl=120, show_spinner="Fetching headlines…")
def _load(sources_key, min_impact, yf_ticker):
    return nc.get_headlines(sources=list(sources_key), min_impact=min_impact,
                            yf_ticker=yf_ticker, limit=80)


if not sources and not yf_ticker:
    st.info("Pick at least one source (or add a Yahoo ticker).")
    st.stop()

# Always fetch at LOW so the alert banner + summary see the full HIGH set,
# then filter for display separately.
items_all = _load(tuple(sources), "LOW", yf_ticker)
floor = nc._IMPACT_ORDER.get(min_impact, 2)
items = [it for it in items_all if nc._IMPACT_ORDER[it["impact"]] <= floor]

# ── HIGH-impact alert banner ────────────────────────────────────────────────────
alerts = nc.high_impact_alerts(items_all, within_min=30.0)
summary = nc.market_impact_summary(items_all)

lvl = summary["level"]
badge = {"elevated": " TAPE HOT", "watch": " WATCH", "calm": " CALM"}[lvl]
sumline = (f"{badge} · {summary['n_high']} HIGH-impact items "
           f"({summary['n_recent_high']} in last hour) · lean **{summary['tone']}**")
if summary["categories"]:
    sumline += " · " + ", ".join(summary["categories"])
if lvl == "elevated":
    st.error(sumline)
elif lvl == "watch":
    st.warning(sumline)
else:
    st.info(sumline)

if alerts:
    st.toast(f"{len(alerts)} HIGH-impact headline(s) in the last 30 min", icon=None)
    with st.container(border=True):
        st.markdown("** BREAKING — HIGH impact, last 30 min**")
        for it in alerts[:6]:
            age = f"{it['age_min']:.0f}m ago" if it.get("age_min") is not None else "—"
            tone = it.get("tone", "neutral")
            title = f"[{it['title']}]({it['link']})" if it.get("link") else it["title"]
            st.markdown(f"- **{title}**  ·  *{it['source']}*  ·  {age}  ·  _{tone}_")

st.divider()

# ── Headline list ───────────────────────────────────────────────────────────────
_IMPACT_TAG = {"HIGH": "[HIGH]", "MEDIUM": "[MED ]", "LOW": "[low ]"}

if not items:
    st.info("No headlines match this filter right now. "
            "Nitter mirrors (Walter Bloomberg) are often rate-limited — try Refresh "
            "or rely on FinancialJuice.")
else:
    st.caption(f"{len(items)} headlines · newest first · "
               "impact = weighted keyword model (rates / inflation / jobs / geopolitics …)")
    for it in items:
        imp = it["impact"]
        tag = _IMPACT_TAG[imp]
        age = f"{it['age_min']:.0f}m" if it.get("age_min") is not None else "—"
        cats = (" · " + ", ".join(it["categories"])) if it.get("categories") else ""
        tone = it.get("tone", "neutral")
        title = f"[{it['title']}]({it['link']})" if it.get("link") else it["title"]
        line = f"`{tag}` {title}  \n  *{it['source']}* · {age} · _{tone}_{cats}"
        if imp == "HIGH":
            st.error(line)
        elif imp == "MEDIUM":
            st.warning(line)
        else:
            st.markdown(line)

st.caption("FinancialJuice via RSS · Walter Bloomberg (@DeItaone) via Nitter mirror "
           "(best-effort) · Yahoo per-ticker. Impact ratings are a heuristic prior — "
           "confirm against price at your GEX levels.")
