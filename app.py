import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import pytz
import json
import os
import streamlit.components.v1 as components
from datetime import datetime, time as dt_time

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
# --- 2. 成交明細模擬快照邏輯 ---
# ===========================================================================
def add_log(msg):
    if "api_logs" not in st.session_state: st.session_state.api_logs = []
    st.session_state.api_logs.insert(0, f"[{now_tw().strftime('%H:%M:%S')}] {msg}")
    st.session_state.api_logs = st.session_state.api_logs[:10]

@st.cache_data(ttl=600)
def get_yesterday_close(sid, token):
    """取得昨收價以計算漲跌幅 (緩存10分鐘以省 Token)"""
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "taiwan_stock_daily",
        "stock_id": sid,
        "date": (now_tw().date()).strftime("%Y-%m-%d"),
        "token": token
    }
    try:
        resp = requests.get(url, params=params, timeout=5)
        data = resp.json().get("data", [])
        if len(data) >= 2: # 抓最後兩筆，倒數第二筆是昨收
            return data[-2].get("close")
        elif len(data) == 1:
            return data[0].get("close")
    except: pass
    return None

def fetch_via_tick_data(sid, token):
    """改抓成交明細 (taiwan_stock_tick) 取最後一筆"""
    if not token: return None
    
    today = now_tw().strftime("%Y-%m-%d")
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": "taiwan_stock_tick",
        "stock_id": sid,
        "date": today,
        "token": token
    }
    
    try:
        resp = requests.get(url, params=params, timeout=7)
        if resp.status_code == 200:
            ticks = resp.json().get("data", [])
            if ticks:
                last_tick = ticks[-1] # 取得最新一筆成交
                price = float(last_tick.get("close", 0))
                
                # 計算漲跌幅 (需比對昨收)
                y_close = get_yesterday_close(sid, token)
                pct = 0.0
                if y_close:
                    pct = round(((price - y_close) / y_close) * 100, 2)
                
                add_log(f"✅ FinMind 明細抓取成功: {sid}")
                return {"price": price, "pct": pct, "source": "FinMind 明細 (最即時)"}
            else:
                add_log(f"⚠️ {sid} 今日尚未有成交明細")
        else:
            add_log(f"❌ 明細接口錯誤: {sid} ({resp.status_code})")
    except Exception as e:
        add_log(f"📡 連線異常: {sid} ({str(e)})")
    
    # 失敗則進 yfinance
    add_log(f"🔄 轉向 yfinance: {sid}")
    try:
        t = yf.Ticker(f"{sid}.TW")
        fast = t.fast_info
        return {
            "price": round(fast.last_price, 2),
            "pct": round(((fast.last_price - fast.previous_close) / fast.previous_close) * 100, 2),
            "source": "yfinance (備援)"
        }
    except: return None

# ===========================================================================
# --- 3. UI 介面 ---
# ===========================================================================
st.set_page_config(page_title="台股監控-成交明細版", layout="centered")

if "initialized" not in st.session_state:
    if os.path.exists("tg_config.json"):
        with open("tg_config.json", "r") as f: cfg = json.load(f)
    else: cfg = {"finmind_token": ""}
    st.session_state.update({**cfg, "initialized": True, "my_stocks": [], "api_logs": []})

browser_id = st.query_params.get("bid", "")
if browser_id and st.session_state.get("last_bid") != browser_id:
    st.session_state.my_stocks = load_user_stocks(browser_id)
    st.session_state.last_bid = browser_id

get_browser_id_component()
if not browser_id: st.stop()

st.title("🤖 台股監控 (成交明細版)")

with st.sidebar:
    st.header("⚙️ 設定")
    fm_token = st.text_input("FinMind Token", value=st.session_state.finmind_token, type="password")
    if st.button("儲存並重新整理"):
        st.session_state.finmind_token = fm_token.strip()
        with open("tg_config.json", "w") as f: json.dump({"finmind_token": fm_token}, f)
        st.rerun()

# 顯示監控清單
for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    q = fetch_via_tick_data(sid, st.session_state.finmind_token)
    
    with st.container(border=True):
        if q:
            price, pct, src = q["price"], q["pct"], q["source"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            c1, c2, c3 = st.columns([4, 3, 2])
            with c1:
                st.markdown(f"#### {sname} `{sid}`")
                st.caption(f"來源: {src}")
            with c2:
                st.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{price}</h2>", unsafe_allow_html=True)
                st.markdown(f"<p style='color:{color}; text-align:right; margin:0;'>{pct}%</p>", unsafe_allow_html=True)
            with c3:
                if st.button("🗑️", key=f"del_{sid}"):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()

# 診斷日誌
st.divider()
with st.expander("📡 API 診斷日誌", expanded=True):
    for log in st.session_state.api_logs:
        st.write(log)

if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
