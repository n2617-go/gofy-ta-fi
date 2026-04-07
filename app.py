import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import pytz
import json
import os
import streamlit.components.v1 as components
from datetime import datetime, time as dt_time, timedelta

# ===========================================================================
# --- 0. 基礎設定 ---
# ===========================================================================
tw_tz = pytz.timezone("Asia/Taipei")
MARKET_OPEN = dt_time(9, 0)
MARKET_CLOSE = dt_time(13, 30)
TG_SAVE_FILE = "tg_config.json"
USER_DATA_DIR = "user_data"
LS_KEY = "tw_stock_browser_id"

os.makedirs(USER_DATA_DIR, exist_ok=True)

def now_tw() -> datetime:
    return datetime.now(tw_tz)

def is_market_open() -> bool:
    n = now_tw()
    if n.weekday() >= 5: return False
    return MARKET_OPEN <= n.time() <= MARKET_CLOSE

# ===========================================================================
# --- 1. 使用者管理 ---
# ===========================================================================
def get_browser_id_component():
    components.html(f"""
    <script>
    (function() {{
        const KEY = "{LS_KEY}";
        let bid = localStorage.getItem(KEY);
        if (!bid) {{
            bid = Math.random().toString(36).slice(2) + Date.now().toString(36);
            localStorage.setItem(KEY, bid);
        }}
        const url = new URL(window.parent.location.href);
        if (url.searchParams.get("bid") !== bid) {{
            url.searchParams.set("bid", bid);
            window.parent.history.replaceState(null, "", url.toString());
            window.parent.location.reload();
        }}
    }})();
    </script>
    """, height=0)

def load_user_stocks(bid: str) -> list:
    path = os.path.join(USER_DATA_DIR, bid + ".json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return [{"id": "2330", "name": "台積電"}]

def save_user_stocks(bid: str, stocks: list):
    path = os.path.join(USER_DATA_DIR, bid + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)

# ===========================================================================
# --- 2. 終極數據抓取邏輯 (修正 422 錯誤) ---
# ===========================================================================
def add_log(msg):
    if "api_logs" not in st.session_state: st.session_state.api_logs = []
    st.session_state.api_logs.insert(0, f"[{now_tw().strftime('%H:%M:%S')}] {msg}")
    st.session_state.api_logs = st.session_state.api_logs[:10]

@st.cache_data(ttl=3600) # 昨收價一小時更新一次即可
def get_yesterday_close_safe(sid, token):
    url = "https://api.finmindtrade.com/api/v4/data"
    # 抓取最近三天的日線，確保能拿到最後一筆交易日作為昨收
    params = {
        "dataset": "taiwan_stock_daily",
        "stock_id": sid,
        "start_date": (now_tw() - timedelta(days=5)).strftime("%Y-%m-%d"),
        "token": token
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        data = resp.json().get("data", [])
        if len(data) >= 2:
            return data[-2].get("close") # 倒數第二筆即為昨收
    except: pass
    return None

def fetch_hybrid_data(sid, token):
    """
    修正後的抓取邏輯：
    1. 嘗試 FinMind 明細 (加上 start_time 避免 422)
    2. 若失敗則 yfinance
    """
    if token:
        # 修正：加上 start_time 只抓取最近 10 分鐘的資料，避免 422 報錯
        start_t = (now_tw() - timedelta(minutes=10)).strftime("%H:%M:%S")
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {
            "dataset": "taiwan_stock_tick",
            "stock_id": sid,
            "date": now_tw().strftime("%Y-%m-%d"),
            "start_time": start_t,
            "token": token
        }
        try:
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code == 200:
                ticks = resp.json().get("data", [])
                if ticks:
                    last_p = float(ticks[-1].get("close", 0))
                    y_close = get_yesterday_close_safe(sid, token)
                    pct = round(((last_p - y_close) / y_close * 100), 2) if y_close else 0.0
                    add_log(f"✅ FinMind 即時成功: {sid}")
                    return {"price": last_p, "pct": pct, "source": "FinMind 即時明細"}
                else:
                    add_log(f"ℹ️ {sid} 近10分鐘無成交")
            else:
                add_log(f"❌ FinMind API 拒絕 ({resp.status_code})")
        except: pass

    # --- 備援系統 ---
    add_log(f"🔄 使用 yfinance 數據: {sid}")
    try:
        t = yf.Ticker(f"{sid}.TW")
        info = t.fast_info
        return {
            "price": round(info.last_price, 2),
            "pct": round(((info.last_price - info.previous_close) / info.previous_close * 100), 2),
            "source": "Yahoo Finance"
        }
    except: return None

# ===========================================================================
# --- 3. UI 渲染 ---
# ===========================================================================
st.set_page_config(page_title="台股監控-終極修復版", layout="centered")

if "initialized" not in st.session_state:
    if os.path.exists(TG_SAVE_FILE):
        with open(TG_SAVE_FILE, "r") as f: cfg = json.load(f)
    else: cfg = {"finmind_token": ""}
    st.session_state.update({**cfg, "initialized": True, "my_stocks": [], "api_logs": []})

browser_id = st.query_params.get("bid", "")
if browser_id and st.session_state.get("last_bid") != browser_id:
    st.session_state.my_stocks = load_user_stocks(browser_id)
    st.session_state.last_bid = browser_id

get_browser_id_component()
if not browser_id: st.stop()

st.title("📈 台股實時監控系統")

# --- 側邊欄 ---
with st.sidebar:
    st.header("⚙️ 系統設定")
    new_token = st.text_input("FinMind Token", value=st.session_state.finmind_token, type="password")
    if st.button("儲存並更新"):
        st.session_state.finmind_token = new_token.strip()
        with open(TG_SAVE_FILE, "w") as f: json.dump({"finmind_token": new_token}, f)
        st.rerun()
    st.divider()
    st.subheader("📡 連線診斷")
    for log in st.session_state.api_logs:
        st.caption(log)

# --- 新增股票 ---
with st.expander("➕ 新增關注股票"):
    c1, c2, c3 = st.columns([2, 2, 1])
    nid = c1.text_input("代號")
    nname = c2.text_input("名稱")
    if c3.button("新增"):
        if nid and nname:
            st.session_state.my_stocks.append({"id": nid, "name": nname})
            save_user_stocks(browser_id, st.session_state.my_stocks)
            st.rerun()

# --- 股票卡片 ---
for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    q = fetch_hybrid_data(sid, st.session_state.finmind_token)
    
    with st.container(border=True):
        if q:
            p, pct, src = q["price"], q["pct"], q["source"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            c1, c2, c3 = st.columns([4, 3, 2])
            with c1:
                st.markdown(f"#### {sname} `{sid}`")
                st.caption(f"來源: {src}")
            with c2:
                st.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{p}</h2>", unsafe_allow_html=True)
                st.markdown(f"<p style='color:{color}; text-align:
