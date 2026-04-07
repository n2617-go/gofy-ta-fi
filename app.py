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

def load_config() -> dict:
    if os.path.exists(TG_SAVE_FILE):
        try:
            with open(TG_SAVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"finmind_token": "", "tg_token": "", "tg_chat_id": ""}

# ===========================================================================
# --- 2. 強化版報價抓取 (避開 'data' KeyError) ---
# ===========================================================================
@st.cache_data(ttl=30)
def fetch_finmind_api_direct(token: str) -> tuple:
    """直接呼叫 FinMind API 避開 SDK Bug"""
    url = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"
    params = {"token": token} if token else {}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        
        if resp.status_code == 403:
            return {}, "❌ Token 權限不足 (403 Forbidden)"
        if resp.status_code != 200:
            return {}, f"❌ API 連線失敗 (Code: {resp.status_code})"
            
        res_json = resp.json()
        # 修正重點：手動檢查資料欄位，不依賴 SDK
        data_list = res_json.get("data", [])
        
        if not data_list:
            return {}, "⚠️ API 回傳資料為空 (可能尚未更新)"
            
        result = {}
        for row in data_list:
            sid = str(row.get("stock_id", ""))
            if sid:
                result[sid] = {
                    "price": float(row.get("close", 0)),
                    "pct": float(row.get("change_rate", 0)),
                    "source": "FinMind 直接 API"
                }
        return result, "✅ FinMind 運作正常"
    except Exception as e:
        return {}, f"❌ 系統錯誤: {str(e)}"

def get_yfinance_quote(sid: str) -> dict:
    try:
        t = yf.Ticker(f"{sid}.TW")
        info = t.fast_info
        return {
            "price": round(info.last_price, 2),
            "pct": round(((info.last_price - info.previous_close) / info.previous_close) * 100, 2),
            "source": "yfinance 備援"
        }
    except:
        return None

# ===========================================================================
# --- 3. UI 介面 ---
# ===========================================================================
st.set_page_config(page_title="台股監控-修復版", layout="centered")

if "initialized" not in st.session_state:
    cfg = load_config()
    st.session_state.update({**cfg, "initialized": True, "my_stocks": []})

browser_id = st.query_params.get("bid", "")
if browser_id and st.session_state.get("last_bid") != browser_id:
    st.session_state.my_stocks = load_user_stocks(browser_id)
    st.session_state.last_bid = browser_id

get_browser_id_component()
if not browser_id: st.stop()

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ 系統設定")
    fm_token_input = st.text_input("FinMind Token", value=st.session_state.finmind_token, type="password")
    if st.button("確認並儲存"):
        st.session_state.finmind_token = fm_token_input.strip()
        cfg = load_config()
        cfg["finmind_token"] = st.session_state.finmind_token
        with open(TG_SAVE_FILE, "w", encoding="utf-8") as f: json.dump(cfg, f)
        st.cache_data.clear()
        st.rerun()

st.title("🤖 台股決策系統 (API 修復版)")

# --- 抓取資料與狀態顯示 ---
all_quotes, status_msg = fetch_finmind_api_direct(st.session_state.finmind_token)

if "✅" in status_msg:
    st.success(status_msg)
else:
    st.warning(status_msg)

# --- 新增股票 ---
with st.expander("➕ 新增關注股票"):
    c1, c2, c3 = st.columns([2, 2, 1])
    n_id = c1.text_input("代號")
    n_name = c2.text_input("名稱")
    if c3.button("新增"):
        if n_id and n_name:
            st.session_state.my_stocks.append({"id": n_id, "name": n_name})
            save_user_stocks(browser_id, st.session_state.my_stocks)
            st.rerun()

# --- 顯示卡片 ---
for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    
    q = all_quotes.get(sid)
    if not q:
        q = get_yfinance_quote(sid)
    
    with st.container(border=True):
        if q:
            price, pct, src = q["price"], q["pct"], q["source"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            
            col_l, col_r, col_btn = st.columns([3, 3, 2])
            with col_l:
                st.markdown(f"### {sname}")
                st.caption(f"代號: `{sid}` | 來源: `{src}`")
            with col_r:
                st.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{price}</h2>", unsafe_allow_html=True)
                st.markdown(f"<p style='color:{color}; text-align:right; margin:0;'>{pct}%</p>", unsafe_allow_html=True)
            with col_btn:
                if st.button("🗑️", key=f"del_{sid}", use_container_width=True):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
        else:
            st.error(f"❌ {sname} ({sid}) 暫無資料")

# 自動更新
if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
