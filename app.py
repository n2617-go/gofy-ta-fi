import streamlit as st
import akshare as ak
import yfinance as yf
import pandas as pd
import requests
import pytz
import json
import os
import streamlit.components.v1 as components
from datetime import datetime, time as dt_time

# ===========================================================================
# --- 0. 基礎設定與環境初始化 ---
# ===========================================================================
tw_tz = pytz.timezone("Asia/Taipei")
MARKET_OPEN = dt_time(9, 0)
MARKET_CLOSE = dt_time(13, 30)
TG_SAVE_FILE = "tg_config.json"
USER_DATA_DIR = "user_data"
LS_KEY = "tw_stock_browser_id"

# 確保儲存目錄存在
os.makedirs(USER_DATA_DIR, exist_ok=True)

def now_tw() -> datetime:
    return datetime.now(tw_tz)

def is_market_open() -> bool:
    n = now_tw()
    if n.weekday() >= 5: return False
    return MARKET_OPEN <= n.time() <= MARKET_CLOSE

# ===========================================================================
# --- 1. 使用者與組態管理 (Local Storage ID) ---
# ===========================================================================
def get_browser_id_component():
    """透過 JS 在瀏覽器儲存唯一 ID，實現多裝置數據隔離"""
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
    path = os.path.join(USER_DATA_DIR, f"{bid}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return [{"id": "2330", "name": "台積電"}]

def save_user_stocks(bid: str, stocks: list):
    path = os.path.join(USER_DATA_DIR, f"{bid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)

def load_config():
    if os.path.exists(TG_SAVE_FILE):
        try:
            with open(TG_SAVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"tg_token": "", "tg_chat_id": ""}

# ===========================================================================
# --- 2. 數據抓取引擎 (AKShare 即時 + yfinance 備援) ---
# ===========================================================================
def add_log(msg):
    if "api_logs" not in st.session_state: st.session_state.api_logs = []
    st.session_state.api_logs.insert(0, f"[{now_tw().strftime('%H:%M:%S')}] {msg}")
    st.session_state.api_logs = st.session_state.api_logs[:10]

def fetch_stock_data(sid: str):
    """優先使用 AKShare 抓取即時數據，失敗則轉向 yfinance"""
    # --- 1. AKShare (新浪接口 - 免 Token 即時報價) ---
    try:
        df = ak.stock_hk_gj_tw_sina(symbol=sid)
        if not df.empty:
            price = float(df.iloc[0]['last'])
            pct = float(df.iloc[0]['pct_change'])
            add_log(f"✅ AKShare 成功: {sid}")
            return {"price": price, "pct": round(pct, 2), "source": "AKShare (即時)"}
    except Exception as e:
        add_log(f"⚠️ AKShare 失敗: {sid}")

    # --- 2. yfinance (備援 - 盤後或 AKShare 異常時使用) ---
    try:
        # 自動判斷上市 (.TW) 或 上櫃 (.TWO)
        target_sid = f"{sid}.TW"
        ticker = yf.Ticker(target_sid)
        fast = ticker.fast_info
        
        if fast.last_price is None or fast.last_price == 0:
            target_sid = f"{sid}.TWO"
            ticker = yf.Ticker(target_sid)
            fast = ticker.fast_info

        if fast.last_price:
            p = fast.last_price
            pc = fast.previous_close
            pct = round(((p - pc) / pc) * 100, 2)
            add_log(f"🔄 yfinance 備援: {sid}")
            return {"price": round(p, 2), "pct": pct, "source": "Yahoo Finance"}
    except:
        add_log(f"❌ 所有來源均失敗: {sid}")
    return None

# ===========================================================================
# --- 3. UI 介面設計 ---
# ===========================================================================
st.set_page_config(page_title="台股 AI 決策監控", layout="centered")

# 初始化 Session State
if "initialized" not in st.session_state:
    cfg = load_config()
    st.session_state.update({**cfg, "initialized": True, "my_stocks": [], "api_logs": []})

# 處理瀏覽器 ID
get_browser_id_component()
browser_id = st.query_params.get("bid", "")

if not browser_id:
    st.info("正在連線至監控伺服器...")
    st.stop()

# 載入用戶數據
if st.session_state.get("last_bid") != browser_id:
    st.session_state.my_stocks = load_user_stocks(browser_id)
    st.session_state.last_bid = browser_id

# --- Sidebar 設定區 ---
with st.sidebar:
    st.header("⚙️ 系統設定")
    st.subheader("Telegram 通知")
    new_tg_token = st.text_input("Bot Token", value=st.session_state.tg_token, type="password")
    new_chat_id = st.text_input("Chat ID", value=st.session_state.tg_chat_id)
    
    if st.button("儲存設定"):
        st.session_state.tg_token = new_tg_token
        st.session_state.tg_chat_id = new_chat_id
        with open(TG_SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump({"tg_token": new_tg_token, "tg_chat_id": new_chat_id}, f)
        st.success("設定已儲存")
    
    st.divider()
    st.subheader("📡 數據診斷日誌")
    for log in st.session_state.api_logs:
        st.caption(log)

st.title("🤖 台股 AI 決策監控系統")

# --- 新增股票區 ---
with st.expander("➕ 新增關注股票", expanded=False):
    c1, c2, c3 = st.columns([2, 2, 1])
    n_id = c1.text_input("股票代號", placeholder="例如: 2330")
    n_name = c2.text_input("股票名稱", placeholder="例如: 台積電")
    if c3.button("新增", use_container_width=True):
        if n_id and n_name:
            if not any(s['id'] == n_id for s in st.session_state.my_stocks):
                st.session_state.my_stocks.append({"id": n_id, "name": n_name})
                save_user_stocks(browser_id, st.session_state.my_stocks)
                st.rerun()

# --- 監控列表區 ---
st.subheader("📋 即時監控清單")
if not st.session_state.my_stocks:
    st.write("目前清單為空，請點擊上方新增。")

for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    
    # 抓取報價
    q = fetch_stock_data(sid)
    
    with st.container(border=True):
        if q:
            price, pct, src = q["price"], q["pct"], q["source"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            arr = "▲" if pct > 0 else "▼" if pct < 0 else "─"
            
            col_info, col_price, col_ctrl = st.columns([4, 3, 2])
            with col_info:
                st.markdown(f"#### {sname}")
                st.caption(f"`{sid}` | 來源: {src}")
            with col_price:
                st.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{price}</h2>", unsafe_allow_html=True)
                st.markdown(f"<p style='color:{color}; text-align:right; margin:0;'>{arr} {abs(pct)}%</p>", unsafe_allow_html=True)
            with col_ctrl:
                if st.button("🗑️", key=f"del_{sid}", use_container_width=True):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
                
                # 排序功能
                b1, b2 = st.columns(2)
                if b1.button("↑", key=f"up_{sid}", use_container_width=True) and idx > 0:
                    st.session_state.my_stocks[idx], st.session_state.my_stocks[idx-1] = st.session_state.my_stocks[idx-1], st.session_state.my_stocks[idx]
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
                if b2.button("↓", key=f"dn_{sid}", use_container_width=True) and idx < len(st.session_state.my_stocks)-1:
                    st.session_state.my_stocks[idx], st.session_state.my_stocks[idx+1] = st.session_state.my_stocks[idx+1], st.session_state.my_stocks[idx]
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
        else:
            st.error(f"❌ {sname} ({sid}) 數據獲取失敗")

# --- 自動更新機制 ---
if is_market_open():
    # 盤中每 60 秒自動刷新網頁
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
    st.toast("盤中實時監控中...", icon="🔄")
