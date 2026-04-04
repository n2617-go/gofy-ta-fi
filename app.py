import streamlit as st
import streamlit.components.v1 as components
import requests
import yfinance as yf
from datetime import datetime
import time
import json
import urllib.parse

st.set_page_config(
    page_title="台股看盤",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# ══════════════════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════════════════
CSS = r"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;900&family=JetBrains+Mono:wght@400;700&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body, [data-testid="stAppViewContainer"] {
    background: #0a0d14 !important;
    color: #e2e8f0 !important;
    font-family: 'Noto Sans TC', sans-serif !important;
}
[data-testid="stAppViewContainer"] {
    background: radial-gradient(ellipse at 20% 0%, #0f1a2e 0%, #0a0d14 60%) !important;
}
#MainMenu, footer, header,
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stSidebarNav"] { display: none !important; }

[data-testid="stAppViewBlockContainer"] {
    padding: 1rem 0.75rem 5rem !important;
    max-width: 480px !important;
    margin: 0 auto !important;
}

.app-header {
    display: flex; align-items: center;
    justify-content: space-between;
    padding: 1rem 0 1.25rem;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    margin-bottom: 1.1rem;
}
.app-title { font-size: 1.35rem; font-weight: 900; letter-spacing: -0.02em; color: #f8fafc; }
.app-title span { color: #38bdf8; }
.app-time {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.68rem; color: #64748b;
    text-align: right; line-height: 1.6;
}
.live-dot {
    display: inline-block; width: 6px; height: 6px;
    border-radius: 50%; background: #22c55e; margin-right: 5px;
    animation: pulse 1.4s ease-in-out infinite;
}
@keyframes pulse {
    0%,100% { opacity:1; transform:scale(1); }
    50%      { opacity:.4; transform:scale(.8); }
}

/* 書籤提示橫幅 */
.bookmark-hint {
    background: linear-gradient(135deg, rgba(56,189,248,0.08), rgba(56,189,248,0.04));
    border: 1px solid rgba(56,189,248,0.2);
    border-radius: 12px;
    padding: 0.65rem 0.9rem;
    margin-bottom: 1rem;
    font-size: 0.72rem;
    color: #94a3b8;
    line-height: 1.6;
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
}
.bookmark-hint-icon { font-size: 0.9rem; flex-shrink: 0; margin-top: 1px; }
.bookmark-hint strong { color: #38bdf8; }

.add-section-title {
    font-size: 0.7rem; font-weight: 700;
    letter-spacing: 0.1em; text-transform: uppercase;
    color: #38bdf8; margin-bottom: 0.7rem;
}

[data-testid="stTextInput"] input {
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid rgba(255,255,255,0.1) !important;
    border-radius: 10px !important;
    color: #f1f5f9 !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 0.9rem !important;
    padding: 0.55rem 0.75rem !important;
}
[data-testid="stTextInput"] input:focus {
    border-color: rgba(56,189,248,0.5) !important;
    box-shadow: 0 0 0 2px rgba(56,189,248,0.1) !important;
}
[data-testid="stTextInput"] label { display: none !important; }

[data-testid="stButton"] button {
    background: linear-gradient(135deg, #0ea5e9, #38bdf8) !important;
    color: #0a0d14 !important;
    font-family: 'Noto Sans TC', sans-serif !important;
    font-weight: 700 !important;
    font-size: 0.82rem !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 0.5rem 1.2rem !important;
    width: 100% !important;
    transition: opacity 0.2s !important;
}
[data-testid="stButton"] button:hover { opacity: 0.85 !important; }

.stock-card {
    background: linear-gradient(135deg, #111827 0%, #0f172a 100%);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 16px;
    padding: 1.1rem 1.1rem 0.9rem;
    margin-bottom: 0.5rem;
    position: relative;
    overflow: hidden;
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
}
.stock-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; height: 2px;
    background: var(--accent, #38bdf8);
    border-radius: 16px 16px 0 0;
}
.stock-card.up   { --accent: #ef4444; }
.stock-card.down { --accent: #22c55e; }
.stock-card.flat { --accent: #94a3b8; }

.card-top {
    display: flex; align-items: flex-start;
    justify-content: space-between; margin-bottom: 0.85rem;
}
.stock-name { font-size: 1.05rem; font-weight: 700; color: #f1f5f9; line-height: 1.3; }
.stock-code { font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; color: #64748b; margin-top: 2px; }
.price-block { text-align: right; }
.price-main {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.6rem; font-weight: 700; line-height: 1; color: #f8fafc;
}
.price-change {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem; font-weight: 700; margin-top: 3px;
}
.up-color   { color: #ef4444; }
.down-color { color: #22c55e; }
.flat-color { color: #94a3b8; }

.ohlc-row {
    display: grid; grid-template-columns: repeat(4,1fr);
    gap: 0.3rem; background: rgba(255,255,255,0.03);
    border-radius: 10px; padding: 0.55rem 0.5rem; margin-bottom: 0.85rem;
}
.ohlc-item { text-align: center; }
.ohlc-label {
    font-size: 0.6rem; color: #475569;
    text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 3px;
}
.ohlc-val {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem; color: #cbd5e1; font-weight: 500;
}

.card-divider { height: 1px; background: rgba(255,255,255,0.05); margin: 0.75rem 0; }

.tech-section-title {
    font-size: 0.65rem; font-weight: 700;
    letter-spacing: 0.1em; text-transform: uppercase;
    color: #475569; margin-bottom: 0.6rem;
}
.kd-row { display: flex; gap: 0.5rem; margin-bottom: 0.65rem; }
.kd-chip {
    flex: 1; background: rgba(255,255,255,0.04);
    border-radius: 8px; padding: 0.45rem 0.6rem; text-align: center;
}
.kd-chip-label { font-size: 0.6rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.06em; }
.kd-chip-val {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1rem; font-weight: 700; color: #e2e8f0; margin-top: 1px;
}
.kd-bar-wrap {
    background: rgba(255,255,255,0.06);
    border-radius: 99px; height: 4px; margin-top: 5px; overflow: hidden;
}
.kd-bar-fill {
    height: 100%; border-radius: 99px;
    background: var(--bar-color, #38bdf8);
    width: var(--bar-width, 50%);
}
.momentum-row {
    display: flex; align-items: center;
    justify-content: space-between;
    background: rgba(255,255,255,0.03);
    border-radius: 8px; padding: 0.4rem 0.65rem; margin-bottom: 0.65rem;
}
.momentum-label { font-size: 0.7rem; color: #64748b; }
.momentum-val { font-family: 'JetBrains Mono', monospace; font-size: 0.78rem; font-weight: 700; }

.signal-row { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
.badge {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 0.72rem; font-weight: 700;
    border-radius: 99px; padding: 0.3rem 0.75rem;
}
.badge-signal-buy   { background: rgba(34,197,94,0.15);  color: #22c55e; border: 1px solid rgba(34,197,94,0.3); }
.badge-signal-sell  { background: rgba(239,68,68,0.15);  color: #ef4444; border: 1px solid rgba(239,68,68,0.3); }
.badge-signal-watch { background: rgba(148,163,184,0.1); color: #94a3b8; border: 1px solid rgba(148,163,184,0.2); }
.badge-trend-up     { background: rgba(251,146,60,0.12); color: #fb923c; border: 1px solid rgba(251,146,60,0.25); }
.badge-trend-down   { background: rgba(96,165,250,0.12); color: #60a5fa; border: 1px solid rgba(96,165,250,0.25); }

.no-data { font-size: 0.75rem; color: #475569; text-align: center; padding: 0.5rem; font-style: italic; }
.error-msg {
    font-size: 0.75rem; color: #f87171;
    background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.2);
    border-radius: 8px; padding: 0.5rem 0.75rem; margin-top: 0.5rem;
}
.success-msg {
    font-size: 0.75rem; color: #4ade80;
    background: rgba(34,197,94,0.08); border: 1px solid rgba(34,197,94,0.2);
    border-radius: 8px; padding: 0.5rem 0.75rem; margin-top: 0.5rem;
}
.card-gap { margin-bottom: 0.9rem; }
.footer-note { text-align: center; font-size: 0.65rem; color: #334155; margin-top: 1.5rem; line-height: 1.7; }
.element-container { margin-bottom: 0 !important; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════
# 持久化核心：query_params  ← Streamlit 原生，100% 可靠
#
# 原理：把 watchlist 編碼成 JSON 存在 URL 的 ?wl=... 參數。
# Streamlit 每次 rerun 都能直接讀到，不依賴 JS 時序。
# 使用者只需把含參數的網址加入書籤，下次直接開書籤即還原。
# ══════════════════════════════════════════════════════════
QP_KEY = "wl"

DEFAULT_STOCKS = [
    {"id": "2330", "name": "台積電"},
    {"id": "2002", "name": "中鋼"},
    {"id": "1326", "name": "台化"},
    {"id": "6505", "name": "台塑化"},
]


def load_watchlist() -> list:
    """從 query_params 讀取 watchlist，讀不到則回傳預設值"""
    try:
        raw = st.query_params.get(QP_KEY, "")
        if raw:
            data = json.loads(raw)
            if isinstance(data, list) and len(data) > 0:
                return data
    except Exception:
        pass
    return DEFAULT_STOCKS.copy()


def save_watchlist(watchlist: list):
    """將 watchlist 寫入 query_params（同步更新網址）"""
    st.query_params[QP_KEY] = json.dumps(watchlist, ensure_ascii=False)


# ══════════════════════════════════════════════════════════
# JS：自動把目前網址（含 ?wl=...）存入 localStorage，
# 下次使用者直接開網址列時自動補回參數（輔助層）
# ══════════════════════════════════════════════════════════
def inject_localstorage_helper():
    components.html("""
    <script>
    (function(){
        var LS_KEY = 'twstock_url_v3';
        try {
            // 如果目前網址有 wl 參數，就存起來
            if (window.parent.location.search.indexOf('wl=') !== -1) {
                localStorage.setItem(LS_KEY, window.parent.location.href);
            } else {
                // 沒有參數時，嘗試從 localStorage 還原
                var saved = localStorage.getItem(LS_KEY);
                if (saved) {
                    var savedUrl = new URL(saved);
                    var wl = savedUrl.searchParams.get('wl');
                    if (wl) {
                        var cur = new URL(window.parent.location.href);
                        cur.searchParams.set('wl', wl);
                        window.parent.history.replaceState({}, '', cur.toString());
                        // 重新載入讓 Streamlit 讀到新的 query_params
                        window.parent.location.reload();
                    }
                }
            }
        } catch(e) {}
    })();
    </script>
    """, height=0)


# ══════════════════════════════════════════════════════════
# 股票名稱查詢
# ══════════════════════════════════════════════════════════
import re as _re

@st.cache_data(ttl=3600)
def fetch_name_map() -> dict:
    """
    從 TWSE / OTC ISIN 頁面抓完整代碼→中文名稱對照表。
    涵蓋上市(mode=2)、上櫃(mode=4)、全數字與含字母代碼（如 00981A）。
    """
    result = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    for mode in ["2", "4"]:
        try:
            r = requests.get(
                f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}",
                headers=headers, timeout=12
            )
            r.encoding = "big5"
            # 逐列解析：每個 <td> 的第一格格式為「代碼　名稱」（全形空格分隔）
            rows = _re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, _re.S)
            for row in rows:
                tds = _re.findall(r'<td[^>]*>(.*?)</td>', row, _re.S)
                if not tds:
                    continue
                # 去除 HTML 標籤
                cell = _re.sub(r'<[^>]+>', '', tds[0]).strip()
                # 支援全形空格 \u3000 與一般空白兩種分隔
                if '\u3000' in cell:
                    parts = cell.split('\u3000', 1)
                else:
                    parts = cell.split(None, 1)   # 以任意空白切一次
                if len(parts) == 2:
                    code = parts[0].strip()
                    name = parts[1].strip()
                    # 代碼：4~7 碼，可包含英文字母（如 00631L、00981A）
                    if code and name and _re.match(r'^[0-9A-Za-z]{4,7}$', code):
                        result[code] = name
        except Exception:
            pass
    return result


@st.cache_data(ttl=300)
def fetch_name_from_twse_api(stock_id: str) -> str:
    """
    用 TWSE 即時 API 查單支股票名稱（上市 + 上櫃）。
    非交易時間仍可查到靜態名稱欄位 'n'。
    快取 5 分鐘。
    """
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/"}
    for market in ["tse", "otc"]:
        try:
            url = (
                "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
                f"?ex_ch={market}_{stock_id}.tw&json=1"
            )
            r = requests.get(url, headers=headers, timeout=8)
            arr = r.json().get("msgArray", [])
            if arr and arr[0].get("n"):
                return arr[0]["n"]
        except Exception:
            pass
    return ""


def get_stock_name(stock_id: str) -> str:
    """
    取得中文名稱，三層備援：
    1. TWSE/OTC 即時 API（最快，非交易時間也有靜態欄位）
    2. ISIN 名稱對照表（最全，含所有 ETF 含字母代碼）
    3. 代碼本身（最後備援）
    """
    # 層 1：即時 API
    name = fetch_name_from_twse_api(stock_id)
    if name:
        return name
    # 層 2：ISIN 對照表
    name_map = fetch_name_map()
    if stock_id in name_map:
        return name_map[stock_id]
    # 層 2b：大小寫不敏感比對（部分 ETF 代碼大小寫不一致）
    sid_upper = stock_id.upper()
    for k, v in name_map.items():
        if k.upper() == sid_upper:
            return v
    return stock_id   # 最後備援


def verify_stock(stock_id: str):
    """
    驗證台股代碼，回傳 (有效: bool, 中文名稱: str)。
    策略：先用即時 API 驗存在性；若非交易時間則改查 ISIN 表；
    最後用 yfinance 確認歷史資料存在。
    """
    # 層 1：TWSE/OTC 即時 API
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/"}
    for market in ["tse", "otc"]:
        try:
            url = (
                "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
                f"?ex_ch={market}_{stock_id}.tw&json=1"
            )
            r = requests.get(url, headers=headers, timeout=8)
            arr = r.json().get("msgArray", [])
            if arr and arr[0].get("n"):
                return True, arr[0]["n"]
        except Exception:
            pass

    # 層 2：ISIN 對照表（含字母代碼、非交易時間皆可查）
    name_map = fetch_name_map()
    sid_upper = stock_id.upper()
    for k, v in name_map.items():
        if k.upper() == sid_upper:
            return True, v

    # 層 3：yfinance 確認存在（名稱從對照表補）
    try:
        ticker = yf.Ticker(f"{stock_id}.TW")
        df = ticker.history(period="5d")
        if not df.empty:
            name = name_map.get(stock_id, stock_id)
            return True, name
    except Exception:
        pass

    return False, ""


# ══════════════════════════════════════════════════════════
# 股價資料
# ══════════════════════════════════════════════════════════
def fetch_twse_realtime(stock_ids: list) -> list:
    tse = [f"tse_{sid}.tw" for sid in stock_ids]
    otc = [f"otc_{sid}.tw" for sid in stock_ids]
    ex_ch = "|".join(tse + otc)
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex_ch}&json=1"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/"}
    try:
        return requests.get(url, headers=headers, timeout=10).json().get("msgArray", [])
    except Exception:
        return []


def fetch_yf_hist(stock_id: str):
    try:
        df = yf.Ticker(f"{stock_id}.TW").history(period="3mo")
        return None if df.empty else df
    except Exception:
        return None


def get_realtime_price(tw, yf_close):
    try:
        z = tw.get("z")
        if z not in ["-", "", None, "0"]:
            return float(z)
        b = tw.get("b")
        if b:
            return float(b.split("_")[0])
        a = tw.get("a")
        if a:
            return float(a.split("_")[0])
    except Exception:
        pass
    return yf_close


def calculate_kd(df, period=9):
    low_min  = df["Low"].rolling(window=period).min()
    high_max = df["High"].rolling(window=period).max()
    rsv = (df["Close"] - low_min) / (high_max - low_min) * 100
    df["K"] = rsv.ewm(com=2).mean()
    df["D"] = df["K"].ewm(com=2).mean()
    return df


def calculate_momentum(df, period=10):
    df["Momentum"] = df["Close"] - df["Close"].shift(period)
    return df


def analyze_signal(df):
    if len(df) < 2:
        return "觀望", "無法判斷"
    l, p = df.iloc[-1], df.iloc[-2]
    sig = "觀望"
    if p["K"] < p["D"] and l["K"] > l["D"]:
        sig = "買進 (黃金交叉)"
    elif p["K"] > p["D"] and l["K"] < l["D"]:
        sig = "賣出 (死亡交叉)"
    trend = "上升動能" if l["Momentum"] > 0 else "下跌動能"
    return sig, trend


def get_stock_data(twse_data, stock):
    code, name = stock["id"], stock["name"]
    tw = next((x for x in twse_data if x.get("c") == code), None)
    df = fetch_yf_hist(code)

    prev_close = open_price = high = low = yf_close = None
    if df is not None and len(df) >= 2:
        prev_close = float(df["Close"].iloc[-2])
        open_price = float(df["Open"].iloc[-1])
        high       = float(df["High"].iloc[-1])
        low        = float(df["Low"].iloc[-1])
        yf_close   = float(df["Close"].iloc[-1])

    price = get_realtime_price(tw, yf_close) if tw else yf_close
    if prev_close is None and tw:
        try:    prev_close = float(tw.get("y") or 0)
        except: pass

    k = d = momentum = None
    signal = trend = "無資料"
    if df is not None:
        df = calculate_kd(df)
        df = calculate_momentum(df)
        signal, trend = analyze_signal(df)
        l = df.iloc[-1]
        k, d, momentum = float(l["K"]), float(l["D"]), float(l["Momentum"])

    change     = (price - prev_close) if (prev_close and price) else 0.0
    change_pct = (change / prev_close * 100) if prev_close else 0.0

    return dict(name=name, code=code, price=price, prev_close=prev_close,
                open=open_price, high=high, low=low,
                change=change, change_pct=change_pct,
                K=k, D=d, Momentum=momentum, signal=signal, trend=trend)


# ══════════════════════════════════════════════════════════
# 畫面輔助
# ══════════════════════════════════════════════════════════
def fmt(v, d=2):
    return f"{v:.{d}f}" if v is not None else "－"

def direction_class(c):
    if c > 0: return "up",   "up-color",   "▲"
    if c < 0: return "down", "down-color",  "▼"
    return "flat", "flat-color", "－"

def sig_cls(s):
    if "買進" in s: return "badge-signal-buy"
    if "賣出" in s: return "badge-signal-sell"
    return "badge-signal-watch"

def trend_cls(t):
    return "badge-trend-up" if "上升" in t else "badge-trend-down"

def kd_bar(val, color):
    pct = min(max(val, 0), 100) if val is not None else 50
    return (
        '<div class="kd-bar-wrap"><div class="kd-bar-fill" style="'
        f'--bar-width:{pct:.0f}%;--bar-color:{color};"></div></div>'
    )


def render_card(row, idx):
    cc, pc, arrow = direction_class(row["change"])

    ohlc = "".join(
        f'<div class="ohlc-item"><div class="ohlc-label">{lb}</div>'
        f'<div class="ohlc-val">{fmt(v)}</div></div>'
        for lb, v in [("昨收", row["prev_close"]), ("開盤", row["open"]),
                      ("最高", row["high"]),        ("最低", row["low"])]
    )

    if row["K"] is not None:
        kd_sec = (
            '<div class="kd-row">'
            '<div class="kd-chip"><div class="kd-chip-label">K 值</div>'
            f'<div class="kd-chip-val">{fmt(row["K"])}</div>{kd_bar(row["K"], "#38bdf8")}</div>'
            '<div class="kd-chip"><div class="kd-chip-label">D 值</div>'
            f'<div class="kd-chip-val">{fmt(row["D"])}</div>{kd_bar(row["D"], "#f472b6")}</div>'
            '</div>'
        )
        mc = "#22c55e" if row["Momentum"] > 0 else "#ef4444"
        mom_sec = (
            '<div class="momentum-row"><span class="momentum-label">動能指標 (10日)</span>'
            f'<span class="momentum-val" style="color:{mc};">{fmt(row["Momentum"])}</span></div>'
        )
        st_text, tr_text = row["signal"], row["trend"]
    else:
        kd_sec  = '<div class="no-data">歷史資料不足，無法計算技術指標</div>'
        mom_sec = ""
        st_text, tr_text = "資料不足", ""

    tr_badge = f'<span class="badge {trend_cls(tr_text)}">{tr_text}</span>' if tr_text else ""
    chg_str  = f'{arrow} {abs(row["change"]):.2f} ({abs(row["change_pct"]):.2f}%)' if row["change"] else "－"

    st.markdown(
        f'<div class="stock-card {cc}">'
        '<div class="card-top"><div>'
        f'<div class="stock-name">{row["name"]}</div>'
        f'<div class="stock-code">{row["code"]} · TW</div>'
        '</div><div class="price-block">'
        f'<div class="price-main">{fmt(row["price"])}</div>'
        f'<div class="price-change {pc}">{chg_str}</div>'
        '</div></div>'
        f'<div class="ohlc-row">{ohlc}</div>'
        '<div class="card-divider"></div>'
        '<div class="tech-section-title">技術指標</div>'
        f'{kd_sec}{mom_sec}'
        f'<div class="signal-row"><span class="badge {sig_cls(st_text)}">{st_text}</span>{tr_badge}</div>'
        '</div><div class="card-gap"></div>',
        unsafe_allow_html=True,
    )

    if st.button(f"移除  {row['name']} ({row['code']})", key=f"del_{row['code']}_{idx}"):
        st.session_state.watchlist = [
            s for s in st.session_state.watchlist if s["id"] != row["code"]
        ]
        save_watchlist(st.session_state.watchlist)
        st.rerun()


# ══════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════

# ① 每次 run 最開始先從 query_params 讀取 watchlist
#    （query_params 在 Streamlit 啟動時就已解析完畢，不存在時序問題）
if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_watchlist()
    # 自動修補：若某支股票的 name 就是 id（代表之前沒查到中文名稱），補回正確名稱
    needs_save = False
    for s in st.session_state.watchlist:
        if s["name"] == s["id"]:
            recovered = get_stock_name(s["id"])
            if recovered != s["id"]:
                s["name"] = recovered
                needs_save = True
    save_watchlist(st.session_state.watchlist)

# ② 注入 localStorage 輔助（把目前網址同步存/還原，為雙重保險）
inject_localstorage_helper()

if "add_msg"  not in st.session_state: st.session_state.add_msg  = ""
if "add_type" not in st.session_state: st.session_state.add_type = ""

now = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

# ── 頂部標題 ─────────────────────────────────────────────
st.markdown(
    '<div class="app-header">'
    '<div class="app-title">📊 台股<span>看盤</span></div>'
    f'<div class="app-time"><span class="live-dot"></span>即時更新<br>{now}</div>'
    '</div>',
    unsafe_allow_html=True,
)

# ── 書籤提示 ─────────────────────────────────────────────
st.markdown(
    '<div class="bookmark-hint">'
    '<span class="bookmark-hint-icon">🔖</span>'
    '<span>將目前網址加入<strong>書籤</strong>，下次直接開啟書籤即可還原您的關注清單</span>'
    '</div>',
    unsafe_allow_html=True,
)

# ── 新增股票 ──────────────────────────────────────────────
with st.expander("➕ 新增關注股票", expanded=False):
    st.markdown(
        '<div class="add-section-title">輸入台股代碼（上市 / 上櫃 / ETF）</div>',
        unsafe_allow_html=True,
    )
    new_id = st.text_input(
        "代碼", placeholder="例如：0050、2454、6669",
        label_visibility="collapsed", key="new_stock_input",
    )
    if st.button("查詢並加入", key="add_btn"):
        cid = new_id.strip()
        if not cid:
            st.session_state.add_msg  = "請輸入股票代碼"
            st.session_state.add_type = "err"
        elif any(s["id"] == cid for s in st.session_state.watchlist):
            st.session_state.add_msg  = f"「{cid}」已在關注清單中"
            st.session_state.add_type = "err"
        else:
            with st.spinner("查詢中…"):
                valid, name = verify_stock(cid)
            if valid:
                st.session_state.watchlist.append({"id": cid, "name": name})
                save_watchlist(st.session_state.watchlist)   # ← 立即寫入 query_params
                st.session_state.add_msg  = f"✅ 已加入「{name}（{cid}）」"
                st.session_state.add_type = "ok"
                st.rerun()
            else:
                st.session_state.add_msg  = f"找不到代碼「{cid}」，請確認為台股代碼"
                st.session_state.add_type = "err"

    if st.session_state.add_msg:
        st.markdown(
            f'<div class="{"success-msg" if st.session_state.add_type == "ok" else "error-msg"}">'
            f'{st.session_state.add_msg}</div>',
            unsafe_allow_html=True,
        )

# ── 股票清單 ──────────────────────────────────────────────
if not st.session_state.watchlist:
    st.markdown(
        '<div style="text-align:center;padding:3rem 1rem;color:#475569;">'
        '<div style="font-size:2.5rem;margin-bottom:0.75rem;">📭</div>'
        '<div style="font-size:0.9rem;font-weight:700;color:#64748b;">關注清單是空的</div>'
        '<div style="font-size:0.75rem;margin-top:0.4rem;">點上方「新增關注股票」來加入</div>'
        '</div>',
        unsafe_allow_html=True,
    )
else:
    ids       = [s["id"] for s in st.session_state.watchlist]
    twse_data = fetch_twse_realtime(ids)
    for idx, stock in enumerate(st.session_state.watchlist):
        row = get_stock_data(twse_data, stock)
        render_card(row, idx)

# ── 頁尾 ──────────────────────────────────────────────────
st.markdown(
    '<div class="footer-note">'
    "資料來源：TWSE 即時 API + Yahoo Finance<br>"
    "每 15 秒自動更新 ／ 僅供參考，不構成投資建議"
    "</div>",
    unsafe_allow_html=True,
)

time.sleep(15)
st.rerun()
