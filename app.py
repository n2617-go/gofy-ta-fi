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
# --- 1. 使用者與組態管理 ---
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
# --- 2. 核心抓取邏輯 (針對 400 錯誤優化) ---
# ===========================================================================
@st.cache_data(ttl=30)
def fetch_finmind_diag(token: str) -> tuple:
    """
    回傳 (數據字典, 狀態標籤, 錯誤詳細訊息)
    """
    url = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"
    
    # 優化：如果 token 為空，完全不要傳入該參數，避免 API 報 400
    params = {}
    if token and len(token.strip()) > 0:
        params["token"] = token.strip()
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        
        # 診斷 HTTP 狀態碼
        if resp.status_code == 400:
            return {}, "❌ 快照階段錯誤 (400)", "請求格式錯誤：請檢查 Token 是否包含非法字元或 API 參數不支援。"
        if resp.status_code == 403:
            return {}, "❌ Token 階段錯誤 (403)", "無權限：您的 Token 可能無效、過期或輸入錯誤。"
        if resp.status_code != 200:
            return {}, f"❌ 連線錯誤 ({resp.status_code})", f"伺服器回傳異常代碼。"

        res_json = resp.json()
        data_list = res_json.get("data", [])
        
        if not data_list:
            # 有些 API 成功但 data 為空會回傳 msg
            api_msg = res_json.get("msg", "無資料內容")
            return {}, "⚠️ API 回傳空值", f"連線成功但無數據：{api_msg}"
            
        result = {}
        for row in data_list:
            sid = str(row.get("stock_id", ""))
            if sid:
                result[sid] = {
                    "price": float(row.get("close", 0)),
                    "pct": float(row.get("change_rate", 0)),
                    "source": "FinMind API"
                }
        return result, "✅ FinMind 連線正常", ""
        
    except Exception as e:
        return {}, "❌ 系統異常", str(e)

def get_yfinance_backup(sid: str):
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
    if st.button("確認並重整"):
        st.session_state.finmind_token = fm_token_input.strip()
        cfg = load_config()
        cfg["finmind_token"] = st.session_state.finmind_token
        with open(TG_SAVE_FILE, "w", encoding="utf-8") as f: json.dump(cfg, f)
        st.cache_data.clear()
        st.rerun()

st.title("🤖 台股監控與診斷系統")

# --- 執行抓取與診斷 ---
all_quotes, status_tag, diag_msg = fetch_finmind_diag(st.session_state.finmind_token)

# 顯示診斷結果
if "✅" in status_tag:
    st.success(f"{status_tag}")
else:
    st.error(f"{status_tag}")
    if diag_msg:
        st.caption(f"診斷訊息: {diag_msg}")

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

# --- 卡片渲染 ---
for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    
    q = all_quotes.get(sid)
    if not q:
        q = get_yfinance_backup(sid)
    
    with st.container(border=True):
        if q:
            price, pct, src = q["price"], q["pct"], q["source"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            
            col_l, col_r, col_btn = st.columns([3, 3, 2])
            with col_l:
                st.markdown(f"### {sname}")
                st.caption(f"`{sid}` | 來源: `{src}`")
            with col_r:
                st.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{price}</h2>", unsafe_allow_html=True)
                st.markdown(f"<p style='color:{color}; text-align:right; margin:0;'>{pct}%</p>", unsafe_allow_html=True)
            with col_btn:
                if st.button("🗑️", key=f"del_{sid}", use_container_width=True):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
        else:
            st.warning(f"⚠️ {sname} ({sid}) 暫無數據")

if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
