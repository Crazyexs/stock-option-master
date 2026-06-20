"""
theme.py — Bloomberg-Terminal look & shared terminal widgets.
================================================================================
Call `theme.apply()` at the top of every page (right after st.set_page_config).
It injects the amber-on-black monospace CSS and renders the live ticker tape.
Also provides `market_monitor()` and the Bloomberg colour constants + a Plotly
layout dict (`PLOTLY`) so every chart matches.

This is *Bloomberg-styled*, not the real Terminal — see the note on the cockpit
page. It themes the UI and adds the signature widgets (tape, monitor, function
menu); it does not replicate Bloomberg's data or functions.
"""

import streamlit as st

# ── Palette ───────────────────────────────────────────────────────────────────
AMBER    = "#FFB000"
ORANGE   = "#FF7A00"
BG       = "#000000"
PANEL    = "#0E0E0E"
GREEN    = "#33FF66"
RED      = "#FF3B30"
DIM      = "#8A8A8A"
OFFWHITE = "#E6E6E6"

# Plotly layout to spread onto every figure for a consistent terminal look.
PLOTLY = dict(
    paper_bgcolor=BG, plot_bgcolor="#050505",
    font=dict(color=AMBER, family="Consolas, 'Courier New', monospace", size=12),
)

# Tape / monitor universe: (label, yfinance ticker).
TAPE = (("ES", "ES=F"), ("NQ", "NQ=F"), ("YM", "YM=F"), ("RTY", "RTY=F"),
        ("GC", "GC=F"), ("CL", "CL=F"), ("VIX", "^VIX"), ("DXY", "DX-Y.NYB"),
        ("US10Y", "^TNX"), ("BTC", "BTC-USD"))

_CSS = """
<style>
:root { --bbg-amber:#FFB000; --bbg-orange:#FF7A00; --bbg-green:#33FF66; --bbg-red:#FF3B30; }
html, body, [class*="css"], .stApp, [data-testid="stAppViewContainer"] {
    background-color:#000000 !important;
    color:#FFB000 !important;
    font-family:'Consolas','Courier New',monospace !important;
}
[data-testid="stHeader"] { background:#000000 !important; border-bottom:1px solid #FF7A00; }
.block-container { padding-top:1rem !important; padding-bottom:2rem !important; }
h1,h2,h3,h4 { color:#FF7A00 !important; font-family:'Consolas',monospace !important;
    letter-spacing:0.5px; text-transform:uppercase; }
h1 { border-bottom:2px solid #FF7A00; padding-bottom:4px; }
a, a:visited { color:#FFD27A !important; }
/* Sidebar */
[data-testid="stSidebar"] { background:#080808 !important; border-right:1px solid #FF7A00; }
[data-testid="stSidebar"] * { color:#FFB000 !important; }
/* Metrics */
[data-testid="stMetric"] { background:#0E0E0E; border:1px solid #2a1a00; border-left:3px solid #FF7A00;
    padding:8px 10px; border-radius:2px; }
[data-testid="stMetricValue"] { color:#FFB000 !important; font-family:'Consolas',monospace !important; }
[data-testid="stMetricLabel"] { color:#8A8A8A !important; text-transform:uppercase; font-size:11px; }
/* Buttons */
.stButton button, .stDownloadButton button {
    background:#1a0e00 !important; color:#FF7A00 !important; border:1px solid #FF7A00 !important;
    border-radius:2px !important; font-family:'Consolas',monospace !important; text-transform:uppercase; }
.stButton button:hover { background:#FF7A00 !important; color:#000 !important; }
/* Tables */
[data-testid="stDataFrame"], [data-testid="stTable"] { background:#050505 !important;
    border:1px solid #2a1a00; font-family:'Consolas',monospace !important; }
/* Inputs */
.stSelectbox div, .stTextInput input, .stRadio label, .stToggle label { color:#FFB000 !important; }
/* Dividers / captions */
hr { border-color:#2a1a00 !important; }
[data-testid="stCaptionContainer"], .stCaption { color:#8A8A8A !important; }

/* Live ticker tape */
.bbg-tape { width:100%; overflow:hidden; background:#0A0A0A; border-top:1px solid #FF7A00;
    border-bottom:1px solid #FF7A00; padding:5px 0; margin:0 0 10px 0; white-space:nowrap; }
.bbg-tape-inner { display:inline-block; padding-left:100%; animation:bbg-scroll 40s linear infinite;
    font-family:'Consolas',monospace; font-size:14px; }
.bbg-tape:hover .bbg-tape-inner { animation-play-state:paused; }
@keyframes bbg-scroll { 0% { transform:translateX(0); } 100% { transform:translateX(-50%); } }
.tk { color:#FFB000; margin:0 6px; }
.tk.up { color:#33FF66; } .tk.dn { color:#FF3B30; }

/* Market monitor table */
.bbg-mon { width:100%; border-collapse:collapse; font-family:'Consolas',monospace; font-size:13px; }
.bbg-mon th { background:#1a0e00; color:#FF7A00; text-align:right; padding:4px 10px;
    border-bottom:1px solid #FF7A00; text-transform:uppercase; }
.bbg-mon td { text-align:right; padding:4px 10px; border-bottom:1px solid #161616; color:#FFB000; }
.bbg-mon td.lbl { text-align:left; color:#FF7A00; font-weight:bold; }
.bbg-mon td.up { color:#33FF66; } .bbg-mon td.dn { color:#FF3B30; }
.bbg-fn { color:#FF7A00; font-family:'Consolas',monospace; }
</style>
"""


@st.cache_data(ttl=60, show_spinner=False)
def _quotes(tape):
    """(label, last, pct-change) for the tape universe — best-effort via yfinance."""
    try:
        import yfinance as yf
        tickers = [t for _, t in tape]
        df = yf.download(tickers, period="2d", interval="1d",
                         progress=False, group_by="ticker", threads=True)
    except Exception:
        return []
    out = []
    for label, tk in tape:
        try:
            c = df[tk]["Close"].dropna()
            if c.empty:
                continue
            last = float(c.iloc[-1])
            prev = float(c.iloc[-2]) if len(c) > 1 else last
            pct = (last / prev - 1.0) * 100.0 if prev else 0.0
            out.append((label, last, pct))
        except Exception:
            continue
    return out


def render_tape():
    q = _quotes(TAPE)
    if not q:
        items = "<span class='tk'>LIVE TAPE UNAVAILABLE — markets closed or feed down</span>"
    else:
        parts = []
        for label, last, pct in q:
            cls = "up" if pct >= 0 else "dn"
            arrow = "▲" if pct >= 0 else "▼"
            parts.append(f"<span class='tk {cls}'>{label} {last:,.2f} {arrow}{abs(pct):.2f}%</span>")
        items = "&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts)
    st.markdown(f"<div class='bbg-tape'><div class='bbg-tape-inner'>{items}"
                f"&nbsp;&nbsp;·&nbsp;&nbsp;{items}</div></div>", unsafe_allow_html=True)


def market_monitor():
    """A Bloomberg-style monitor grid of the universe."""
    q = _quotes(TAPE)
    if not q:
        st.info("Market monitor unavailable (feed down / markets closed).")
        return
    rows = ""
    for label, last, pct in q:
        cls = "up" if pct >= 0 else "dn"
        rows += (f"<tr><td class='lbl'>{label}</td><td>{last:,.2f}</td>"
                 f"<td class='{cls}'>{pct:+.2f}%</td></tr>")
    st.markdown(f"<table class='bbg-mon'><thead><tr><th>SECURITY</th><th>LAST</th>"
                f"<th>CHG%</th></tr></thead><tbody>{rows}</tbody></table>",
                unsafe_allow_html=True)


def style_fig(fig):
    """Apply the terminal Plotly layout to any figure (safe no-op on None)."""
    if fig is not None:
        try:
            fig.update_layout(**PLOTLY)
        except Exception:
            pass
    return fig


def apply(header: bool = True):
    """Inject CSS + (optionally) the live ticker tape. Call after set_page_config."""
    st.markdown(_CSS, unsafe_allow_html=True)
    if header:
        render_tape()
