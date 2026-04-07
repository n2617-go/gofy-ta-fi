import streamlit as st
import yfinance as yf
import pandas as pd
import requests
import pytz
import json
import os
import streamlit.components.v1 as components
from datetime import datetime, time as dt_time, timedelta
from FinMind.data import DataLoader
from ta.trend import SMAIndicator, MACD
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands

# ===========================================================================
# --- 0. 基礎設定 ---
# ===========================================================================
tw_tz = pytz.timezone("Asia/Taipei")
MARKET_OPEN = dt_time(9, 0)
MARKET_CLOSE = dt_time(13, 30)
TG_SAVE_FILE = "tg_config.json"
USER_DATA_DIR = "user_data"
ALERT_DIR = "alert_state"
LS_KEY = "tw_stock_browser_id"
DEFAULT_STOCKS = [{"id": "2330", "name": "台積電"}]

os.makedirs(USER_DATA_DIR, exist_ok=True)
os.makedirs(ALERT_DIR, exist_ok=True)

def now_tw() -> datetime:
    return datetime.now(tw_tz)

def is_market_open() -> bool:
    n = now_tw()
    if n.weekday() >= 5: return False
    return MARKET_OPEN <= n.time() <= MARKET_CLOSE

def today_str() -> str:
    return now_tw().strftime("%Y-%m-%d")

# ===========================================================================
# --- 1. 使用者與資料儲存 ---
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

def user_file(bid: str) -> str:
    safe_bid = "".join(c for c in bid if c.isalnum() or c in "-_")[:64]
    return os.path.join(USER_DATA_DIR, safe_bid + ".json")

def load_user_stocks(bid: str) -> list:
    path = user_file(bid)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else list(DEFAULT_STOCKS)
        except: pass
    return list(DEFAULT_STOCKS)

def save_user_stocks(bid: str, stocks: list):
    with open(user_file(bid), "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)

def load_tg_config() -> dict:
    if os.path.exists(TG_SAVE_FILE):
        try:
            with open(TG_SAVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"tg_token": "", "tg_chat_id": "", "tg_threshold": 3.0, "tg_reset": 1.0, "finmind_token": ""}

# ===========================================================================
# --- 2. 報價抓取核心 (強化版) ---
# ===========================================================================
def get_finmind_loader():
    dl = DataLoader()
    token = st.session_state.get("finmind_token", "")
    if token: dl.login_by_token(api_token=token)
    return dl

@st.cache_data(ttl=30) # 縮短緩存時間至 30 秒
def fetch_all_finmind_quotes() -> dict:
    try:
        dl = get_finmind_loader()
        df = dl.taiwan_stock_tick_snapshot(stock_id="")
        if df is not None and not df.empty:
            return {str(row["stock_id"]): {"price": float(row["close"]), "pct": float(row["change_rate"]), "open": float(row["open"])} 
                    for _, row in df.iterrows()}
    except: pass
    return {}

def get_single_quote_backup(sid: str) -> dict:
    """如果 FinMind 失敗，改用 yfinance 當備援"""
    try:
        ticker = yf.Ticker(f"{sid}.TW")
        info = ticker.fast_info
        last_price = info.last_price
        prev_close = info.previous_close
        pct = round(((last_price - prev_close) / prev_close) * 100, 2)
        return {"price": round(last_price, 2), "pct": pct, "open": info.open}
    except:
        return None

# ===========================================================================
# --- 3. 介面設定 ---
# ===========================================================================
st.set_page_config(page_title="台股決策系統", layout="centered")

if "initialized" not in st.session_state:
    cfg = load_tg_config()
    st.session_state.update({**cfg, "initialized": True, "my_stocks": []})

browser_id = st.query_params.get("bid", "")
if browser_id and st.session_state.get("last_bid") != browser_id:
    st.session_state.my_stocks = load_user_stocks(browser_id)
    st.session_state.last_bid = browser_id

get_browser_id_component()
if not browser_id: st.stop()

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ 設定")
    col1, col2 = st.columns([3, 1])
    with col1:
        fm_token = st.text_input("FinMind Token", value=st.session_state.finmind_token, type="password")
    with col2:
        st.write(" ")
        st.write(" ")
        if st.button("確認"):
            st.session_state.finmind_token = fm_token
            # 儲存到檔案
            cfg = load_tg_config()
            cfg["finmind_token"] = fm_token
            with open(TG_SAVE_FILE, "w", encoding="utf-8") as f: json.dump(cfg, f)
            st.cache_data.clear()
            st.rerun()
    
    st.session_state.tg_token = st.text_input("Telegram Bot Token", value=st.session_state.tg_token)
    st.session_state.tg_chat_id = st.text_input("Chat ID", value=st.session_state.tg_chat_id)
    if st.button("儲存通知設定"):
        cfg = load_tg_config()
        cfg.update({"tg_token": st.session_state.tg_token, "tg_chat_id": st.session_state.tg_chat_id})
        with open(TG_SAVE_FILE, "w", encoding="utf-8") as f: json.dump(cfg, f)
        st.success("設定已儲存")

st.title("🤖 台股 AI 技術決策系統")

# --- 股票輸入欄位 (恢復) ---
with st.expander("➕ 新增關注股票", expanded=True):
    c1, c2, c3 = st.columns([2, 2, 1])
    new_id = c1.text_input("股票代號", placeholder="例如: 2330")
    new_name = c2.text_input("股票簡稱", placeholder="例如: 台積電")
    if c3.button("新增", use_container_width=True):
        if new_id and new_name:
            if not any(s['id'] == new_id for s in st.session_state.my_stocks):
                st.session_state.my_stocks.append({"id": new_id, "name": new_name})
                save_user_stocks(browser_id, st.session_state.my_stocks)
                st.rerun()

# --- 渲染卡片 ---
st.subheader("📋 我的追蹤清單")
all_fm_quotes = fetch_all_finmind_quotes()

if not st.session_state.my_stocks:
    st.info("清單目前是空的，請先新增股票。")

for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    
    # 嘗試抓取報價 (FinMind -> yfinance)
    q = all_fm_quotes.get(sid)
    if not q:
        q = get_single_quote_backup(sid)
    
    with st.container(border=True):
        if q:
            price, pct = q["price"], q["pct"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            arr = "▲" if pct > 0 else "▼" if pct < 0 else "─"
            
            # 卡片標題區
            col_t, col_p, col_ctrl = st.columns([3, 3, 2])
            col_t.markdown(f"### {sname}\n`{sid}`")
            col_p.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{price}</h2>", unsafe_allow_html=True)
            col_p.markdown(f"<p style='color:{color}; text-align:right; margin:0;'>{arr} {abs(pct)}%</p>", unsafe_allow_html=True)
            
            # 操作按鈕
            with col_ctrl:
                if st.button("🗑️", key=f"del_{sid}", use_container_width=True):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
                
                # 排序按鈕
                b_u, b_d = st.columns(2)
                if b_u.button("↑", key=f"up_{sid}", use_container_width=True) and idx > 0:
                    st.session_state.my_stocks[idx], st.session_state.my_stocks[idx-1] = st.session_state.my_stocks[idx-1], st.session_state.my_stocks[idx]
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
                if b_d.button("↓", key=f"dn_{sid}", use_container_width=True) and idx < len(st.session_state.my_stocks)-1:
                    st.session_state.my_stocks[idx], st.session_state.my_stocks[idx+1] = st.session_state.my_stocks[idx+1], st.session_state.my_stocks[idx]
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
        else:
            st.error(f"❌ {sname} ({sid}) 報價抓取失敗")
            if st.button("刪除此項", key=f"del_err_{sid}"):
                st.session_state.my_stocks.pop(idx)
                save_user_stocks(browser_id, st.session_state.my_stocks)
                st.rerun()

# --- 自動刷新 ---
if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
    st.toast("即時報價更新中...", icon="🔄")
