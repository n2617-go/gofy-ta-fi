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
tw_tz         = pytz.timezone("Asia/Taipei")
MARKET_OPEN      = dt_time(9, 0)
MARKET_CLOSE     = dt_time(13, 30)
AFTERHOURS_START = dt_time(14, 0)   
TG_SAVE_FILE  = "tg_config.json"
USER_DATA_DIR = "user_data"
ALERT_DIR     = "alert_state"
LS_KEY        = "tw_stock_browser_id"
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
# --- 1. 使用者與資料儲存邏輯 ---
# ===========================================================================
def get_browser_id_component():
    components.html(f"""
    <script>
    (function() {{
        const KEY = "{LS_KEY}";
        let bid = localStorage.getItem(KEY);
        if (!bid) {{
            bid = (typeof crypto !== "undefined" && crypto.randomUUID)
                  ? crypto.randomUUID()
                  : Math.random().toString(36).slice(2) + Date.now().toString(36);
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

def safe_bid(bid: str) -> str:
    return "".join(c for c in bid if c.isalnum() or c in "-_")[:64]

def user_file(bid: str) -> str:
    return os.path.join(USER_DATA_DIR, safe_bid(bid) + ".json")

def load_user_stocks(bid: str) -> list:
    path = user_file(bid)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list): return data
        except: pass
    return list(DEFAULT_STOCKS)

def save_user_stocks(bid: str, stocks: list):
    try:
        with open(user_file(bid), "w", encoding="utf-8") as f:
            json.dump(stocks, f, ensure_ascii=False, indent=2)
    except: pass

def load_tg_config() -> dict:
    if os.path.exists(TG_SAVE_FILE):
        try:
            with open(TG_SAVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"tg_token": "", "tg_chat_id": "", "tg_threshold": 3.0, "tg_reset": 1.0, "finmind_token": ""}

def save_tg_config():
    with open(TG_SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "tg_token": st.session_state.tg_token,
            "tg_chat_id": st.session_state.tg_chat_id,
            "tg_threshold": st.session_state.tg_threshold,
            "tg_reset": st.session_state.tg_reset,
            "finmind_token": st.session_state.finmind_token,
        }, f, ensure_ascii=False, indent=4)

# ===========================================================================
# --- 2. 報價抓取核心 ---
# ===========================================================================
def get_finmind_loader():
    dl = DataLoader()
    token = st.session_state.get("finmind_token", "")
    if token: dl.login_by_token(api_token=token)
    return dl

@st.cache_data(ttl=60)
def fetch_all_quotes() -> dict:
    try:
        dl = get_finmind_loader()
        df = dl.taiwan_stock_tick_snapshot(stock_id="")
        if df is None or df.empty: return {}
        return {str(row["stock_id"]): {"price": float(row["close"]), "pct": float(row["change_rate"]), "open": float(row["open"])} 
                for _, row in df.iterrows()}
    except: return {}

@st.cache_data(ttl=60)
def fetch_single_quote(stock_id: str) -> dict:
    try:
        dl = get_finmind_loader()
        df = dl.taiwan_stock_tick_snapshot(stock_id=stock_id)
        if df is None or df.empty: return {}
        row = df.iloc[-1]
        return {"price": float(row["close"]), "pct": float(row["change_rate"]), "open": float(row["open"])}
    except: return {}

# ===========================================================================
# --- 3. 通知發送與技術分析 ---
# ===========================================================================
def send_telegram_msg(msg: str):
    token = st.session_state.get("tg_token")
    chat_id = st.session_state.get("tg_chat_id")
    if not token or not chat_id: return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": msg}, timeout=5)
    except: pass

def get_tech_rating(df: pd.DataFrame):
    if len(df) < 20: return "無資料", "N/A"
    close = df['Close']
    ma5, ma20 = close.rolling(5).mean().iloc[-1], close.rolling(20).mean().iloc[-1]
    rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
    
    if close.iloc[-1] > ma5 > ma20 and rsi > 50: return "Strong Buy", "🔥 強力看多"
    if close.iloc[-1] > ma20: return "Buy", "📈 偏多"
    if close.iloc[-1] < ma5 < ma20 and rsi < 50: return "Strong Sell", "💣 強力看空"
    return "Neutral", "➡️ 中性觀察"

# ===========================================================================
# --- 4. 主程式 UI ---
# ===========================================================================
st.set_page_config(page_title="台股決策系統", layout="centered")

# 初始化 session_state
if "initialized" not in st.session_state:
    cfg = load_tg_config()
    st.session_state.update({**cfg, "initialized": True, "my_stocks": []})

browser_id = st.query_params.get("bid", "")
if browser_id and st.session_state.get("last_bid") != browser_id:
    st.session_state.my_stocks = load_user_stocks(browser_id)
    st.session_state.last_bid = browser_id

get_browser_id_component()
if not browser_id: st.stop()

# 側邊欄設定
with st.sidebar:
    st.header("⚙️ 系統設定")
    col1, col2 = st.columns([3, 1])
    with col1:
        new_fm_token = st.text_input("FinMind Token", value=st.session_state.finmind_token, type="password")
    with col2:
        st.write(" ") 
        st.write(" ") 
        if st.button("確認"):
            st.session_state.finmind_token = new_fm_token
            save_tg_config()
            st.rerun()
    
    st.session_state.tg_token = st.text_input("TG Bot Token", value=st.session_state.tg_token)
    st.session_state.tg_chat_id = st.text_input("TG Chat ID", value=st.session_state.tg_chat_id)
    if st.button("儲存通知設定"):
        save_tg_config()
        st.success("已儲存")

st.title("🤖 台股 AI 技術分級決策")

# --- 新增股票輸入欄位 ---
with st.expander("➕ 新增關注股票", expanded=True):
    col_sid, col_name, col_btn = st.columns([2, 2, 1])
    new_id = col_sid.text_input("股票代號 (如: 2330)")
    new_name = col_name.text_input("簡稱 (如: 台積電)")
    if col_btn.button("新增", use_container_width=True):
        if new_id and new_name:
            if not any(s['id'] == new_id for s in st.session_state.my_stocks):
                st.session_state.my_stocks.append({"id": new_id, "name": new_name})
                save_user_stocks(browser_id, st.session_state.my_stocks)
                st.rerun()

# --- 股票卡片渲染 ---
all_quotes = fetch_all_quotes()

for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    q = all_quotes.get(sid) or fetch_single_quote(sid)
    
    with st.container(border=True):
        if q:
            price, pct = q["price"], q["pct"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            
            c1, c2, c3 = st.columns([2, 2, 1])
            c1.markdown(f"### {sname} ({sid})")
            c2.markdown(f"<h2 style='color:{color}; text-align:right;'>{price} ({pct}%)</h2>", unsafe_allow_html=True)
            
            # 刪除與排序按鈕
            if c3.button("🗑️", key=f"del_{sid}"):
                st.session_state.my_stocks.pop(idx)
                save_user_stocks(browser_id, st.session_state.my_stocks)
                st.rerun()
            
            # 這裡可以加入您的 yfinance 技術分析邏輯 (與原本相同)
            # 例如: df = yf.download(f"{sid}.TW", period="1mo")...
        else:
            st.warning(f"⚠️ {sname} ({sid}) 報價取得失敗，請檢查代號或 Token 限制。")
            if st.button("刪除", key=f"del_err_{sid}"):
                st.session_state.my_stocks.pop(idx)
                save_user_stocks(browser_id, st.session_state.my_stocks)
                st.rerun()

if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
