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
# --- 2. 終極修復報價邏輯 (針對 400 錯誤優化) ---
# ===========================================================================
@st.cache_data(ttl=30)
def fetch_finmind_api_v4(token: str) -> tuple:
    """直接使用 Requests 存取 V4 API，並進行深度診斷"""
    api_url = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"
    
    # 這裡很關鍵：去除 Token 前後的隱形換行或空白
    clean_token = token.strip() if token else ""
    
    # 建立參數字典：只有當 Token 有長度時才加入
    params = {}
    if clean_token:
        params["token"] = clean_token
    
    try:
        # 使用 verify=True 確保安全性，增加 timeout 避免卡死
        response = requests.get(api_url, params=params, timeout=10)
        
        # 診斷 HTTP 狀態
        status = response.status_code
        if status == 400:
            # 嘗試抓取 API 給出的具體錯誤原因
            try:
                err_detail = response.json().get("msg", "參數格式錯誤")
            except:
                err_detail = "API 拒絕請求 (可能是 Token 包含特殊非法字元)"
            return {}, f"❌ 快照階段錯誤 (400)", err_detail
            
        if status == 403:
            return {}, "❌ Token 階段錯誤 (403)", "無權限：請檢查 Token 是否複製完整或已過期。"
            
        if status != 200:
            return {}, f"❌ 連線異常 ({status})", "伺服器暫時無法回應。"

        # 解析資料
        res_data = response.json()
        raw_list = res_data.get("data", [])
        
        if not raw_list:
            return {}, "⚠️ 暫無數據", "連線成功，但 API 目前未回傳任何股票快照。"
            
        # 轉換成 ID 對應的字典
        quotes = {}
        for item in raw_list:
            sid = str(item.get("stock_id", ""))
            if sid:
                quotes[sid] = {
                    "price": float(item.get("close", 0)),
                    "pct": float(item.get("change_rate", 0)),
                    "source": "FinMind API"
                }
        return quotes, "✅ FinMind 運作正常", ""
        
    except Exception as e:
        return {}, "❌ 系統錯誤", str(e)

def fetch_yfinance_backup(sid: str):
    """Yahoo Finance 最後防線"""
    try:
        ticker = yf.Ticker(f"{sid}.TW")
        fast = ticker.fast_info
        return {
            "price": round(fast.last_price, 2),
            "pct": round(((fast.last_price - fast.previous_close) / fast.previous_close) * 100, 2),
            "source": "yfinance 備援"
        }
    except:
        return None

# ===========================================================================
# --- 3. UI 介面 ---
# ===========================================================================
st.set_page_config(page_title="台股監控-終極修復", layout="centered")

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
    st.header("⚙️ 設定中心")
    new_fm_token = st.text_input("FinMind API Token", value=st.session_state.finmind_token, type="password", help="請從 FinMind 官網複製 Token")
    
    if st.button("💾 儲存並強制重整"):
        st.session_state.finmind_token = new_fm_token.strip()
        # 同步到檔案
        cfg = load_config()
        cfg["finmind_token"] = st.session_state.finmind_token
        with open(TG_SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        st.cache_data.clear() # 關鍵：清除舊的 400 錯誤緩存
        st.rerun()

# --- 抓取與診斷 ---
st.title("🤖 台股決策監控系統")

# 這裡執行最新的 API 抓取邏輯
all_quotes, status_tag, err_detail = fetch_finmind_api_v4(st.session_state.finmind_token)

# 狀態提示區
if "✅" in status_tag:
    st.success(f"**{status_tag}**")
else:
    st.error(f"**{status_tag}**")
    if err_detail:
        st.info(f"💡 錯誤原因解析：{err_detail}")

# --- 新增股票 ---
with st.expander("➕ 新增關注股票", expanded=False):
    c1, c2, c3 = st.columns([2, 2, 1])
    n_id = c1.text_input("股票代號", placeholder="例如: 2330")
    n_name = c2.text_input("股票名稱", placeholder="例如: 台積電")
    if c3.button("新增", use_container_width=True):
        if n_id and n_name:
            st.session_state.my_stocks.append({"id": n_id, "name": n_name})
            save_user_stocks(browser_id, st.session_state.my_stocks)
            st.rerun()

# --- 卡片渲染 ---
st.subheader("📋 我的監控清單")
for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    
    # 優先嘗試 API 數據，失敗才進備援
    q = all_quotes.get(sid)
    if not q:
        q = fetch_yfinance_backup(sid)
    
    with st.container(border=True):
        if q:
            price, pct, src = q["price"], q["pct"], q["source"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            arr = "▲" if pct > 0 else "▼" if pct < 0 else "─"
            
            c_info, c_price, c_del = st.columns([3, 3, 2])
            with c_info:
                st.markdown(f"#### {sname}")
                st.caption(f"代號: `{sid}`")
                st.caption(f"來源: `{src}`")
            with c_price:
                st.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{price}</h2>", unsafe_allow_html=True)
                st.markdown(f"<p style='color:{color}; text-align:right; margin:0;'>{arr} {abs(pct)}%</p>", unsafe_allow_html=True)
            with c_del:
                if st.button("🗑️", key=f"del_{sid}", use_container_width=True):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
        else:
            st.warning(f"⚠️ {sname} ({sid}) 資料暫時無法取得")

if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
