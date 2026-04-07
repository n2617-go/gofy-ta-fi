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
    if n.weekday() >= 5:
        return False
    return MARKET_OPEN <= n.time() <= MARKET_CLOSE

def is_after_hours() -> bool:
    n    = now_tw()
    t    = n.time()
    wday = n.weekday()
    if wday >= 5: return True
    if t >= AFTERHOURS_START: return True
    if t < MARKET_OPEN: return True
    return False

def today_str() -> str:
    return now_tw().strftime("%Y-%m-%d")

# ===========================================================================
# --- 1. 使用者識別 ---
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

# ===========================================================================
# --- 2. 使用者股票清單 ---
# ===========================================================================
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
        except Exception: pass
    return list(DEFAULT_STOCKS)

def save_user_stocks(bid: str, stocks: list):
    try:
        with open(user_file(bid), "w", encoding="utf-8") as f:
            json.dump(stocks, f, ensure_ascii=False, indent=2)
    except Exception: pass

# ===========================================================================
# --- 3. 通知狀態管理 ---
# ===========================================================================
def alert_state_file(bid: str) -> str:
    return os.path.join(ALERT_DIR, safe_bid(bid) + "_alert.json")

def load_alert_state(bid: str) -> dict:
    path = alert_state_file(bid)
    today = today_str()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today: return data
        except Exception: pass
    return {"date": today, "states": {}}

def save_alert_state(bid: str, state: dict):
    try:
        with open(alert_state_file(bid), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception: pass

# ===========================================================================
# --- 4. Telegram + FinMind 設定 ---
# ===========================================================================
def load_tg_config() -> dict:
    if os.path.exists(TG_SAVE_FILE):
        try:
            with open(TG_SAVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    return {
        "tg_token": "", "tg_chat_id": "",
        "tg_threshold": 3.0, "tg_reset": 1.0,
        "finmind_token": "",
    }

def save_tg_config():
    with open(TG_SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "tg_token":      st.session_state.tg_token,
            "tg_chat_id":    st.session_state.tg_chat_id,
            "tg_threshold":  st.session_state.tg_threshold,
            "tg_reset":      st.session_state.tg_reset,
            "finmind_token": st.session_state.finmind_token,
        }, f, ensure_ascii=False, indent=4)

# ===========================================================================
# --- 5. session_state 初始化 ---
# ===========================================================================
if "initialized" not in st.session_state:
    tg_cfg = load_tg_config()
    st.session_state.update({
        "tg_token":      tg_cfg["tg_token"],
        "tg_chat_id":    tg_cfg["tg_chat_id"],
        "tg_threshold":  tg_cfg.get("tg_threshold", 3.0),
        "tg_reset":      tg_cfg.get("tg_reset", 1.0),
        "finmind_token": tg_cfg.get("finmind_token", ""),
        "initialized":   True,
        "hist_cache":    {},   
        "quote_cache":   {},   
        "my_stocks":     list(DEFAULT_STOCKS),
    })

browser_id = st.query_params.get("bid", "")
if browser_id and st.session_state.get("stocks_loaded_bid") != browser_id:
    st.session_state.my_stocks        = load_user_stocks(browser_id)
    st.session_state.stocks_loaded_bid = browser_id

# ===========================================================================
# --- 6. FinMind 報價抓取 ---
# ===========================================================================
def get_finmind_loader():
    dl = DataLoader()
    token = st.session_state.get("finmind_token", "")
    if token:
        dl.login_by_token(api_token=token)
    return dl

@st.cache_data(ttl=60)
def fetch_all_quotes() -> dict:
    try:
        dl = get_finmind_loader()
        df = dl.taiwan_stock_tick_snapshot(stock_id="")
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            sid = str(row.get("stock_id", ""))
            if not sid: continue
            try:
                result[sid] = {
                    "price": float(row.get("close", 0)),
                    "pct":   float(row.get("change_rate", 0)),
                    "open":  float(row.get("open", 0))
                }
            except: continue
        return result
    except Exception as e:
        return {}

@st.cache_data(ttl=60)
def fetch_single_quote(stock_id: str) -> dict:
    try:
        dl = get_finmind_loader()
        df = dl.taiwan_stock_tick_snapshot(stock_id=stock_id)
        if df is None or df.empty: return {}
        row = df.iloc[-1]
        return {
            "price": float(row.get("close", 0)),
            "pct":   float(row.get("change_rate", 0)),
            "open":  float(row.get("open", 0)),
        }
    except: return {}

def get_quote(stock_id: str) -> dict:
    quotes = fetch_all_quotes()
    if stock_id in quotes:
        return quotes[stock_id]
    return fetch_single_quote(stock_id)

# ===========================================================================
# --- 7. 動能與技術分析 (略，與原版邏輯相同但確保穩定) ---
# ===========================================================================
def classify_short_implication(pct, ratio, tg_threshold):
    is_up, is_down = pct >= tg_threshold, pct <= -tg_threshold
    is_vol_up, is_vol_down = ratio >= 1.5, ratio < 1.0
    if is_up and is_vol_up: return "🚀 短線意涵：帶量突破"
    if is_up and is_vol_down: return "⚠️ 短線意涵：虛假拉抬"
    if is_down and is_vol_up: return "💣 短線意涵：帶量殺盤"
    if is_down and is_vol_down: return "🔍 短線意涵：洗盤觀察"
    return ""

def fetch_momentum_analysis(stock_id, pct=0.0, tg_threshold=3.0):
    try:
        dl = get_finmind_loader()
        today = today_str()
        df = dl.taiwan_stock_minute(stock_id=stock_id, start_date=today, end_date=today)
        if df is None or df.empty: return {}
        vol_col = next((c for c in ["volume", "Volume", "vol"] if c in df.columns), None)
        if not vol_col: return {}
        df = df.sort_values("date") if "date" in df.columns else df
        df[vol_col] = pd.to_numeric(df[vol_col], errors="coerce").fillna(0)
        recent = df.tail(6)
        if len(recent) < 2: return {}
        cur_vol, avg_vol = float(recent.iloc[-1][vol_col]), float(recent.iloc[:-1][vol_col].mean())
        ratio = cur_vol / avg_vol if avg_vol > 0 else 0.0
        label = "🔥 爆量" if ratio >= 2.0 else "📈 放量" if ratio >= 1.5 else "➡️ 正常" if ratio >= 1.0 else "📉 縮量"
        return {
            "cur_vol": int(cur_vol), "avg_vol": int(avg_vol), "ratio": round(ratio, 2),
            "momentum_label": f"{label}（{ratio:.1f}倍）", "short_impl": classify_short_implication(pct, ratio, tg_threshold)
        }
    except: return {}

# (此處保留原程式碼中的 8-15 節邏輯，為節省篇幅僅修正規範 UI 部分)

# ===========================================================================
# --- 16. 介面 (包含新增的 Token 確認按鈕) ---
# ===========================================================================
st.set_page_config(page_title="台股決策系統 V7.6", layout="centered")

# CSS (略，保持您的美化設定)
st.markdown("""<style>...</style>""", unsafe_allow_html=True) # 此處保持原有的 CSS 內容

st.title("🤖 台股 AI 技術分級決策支援")

if not browser_id:
    get_browser_id_component()
    st.info("⏳ 初始化中，請稍候...")
    st.stop()

if is_market_open():
    components.html("<script>setTimeout(function() { window.parent.location.reload(); }, 60000);</script>", height=0)
    st.success("🟢 **開盤中** — 每 60 秒自動更新")
else:
    st.info(f"🔵 **非開盤時間** ({now_tw().strftime('%H:%M')})")

# ── Sidebar ──
with st.sidebar:
    st.header("⚙️ 設定")
    st.subheader("📡 FinMind")
    
    # 增加輸入框與確認按鈕
    col_t1, col_t2 = st.columns([3, 1])
    with col_t1:
        new_token = st.text_input("API Token", type="password", value=st.session_state.finmind_token)
    with col_t2:
        st.write("") # 佔位
        st.write("") # 佔位
        if st.button("確認"):
            st.session_state.finmind_token = new_token
            save_tg_config()
            st.cache_data.clear()
            st.success("Token 已更新")
            st.rerun()
            
    st.divider()
    st.subheader("🔔 Telegram 通知")
    st.session_state.tg_token = st.text_input("Bot Token", type="password", value=st.session_state.tg_token)
    st.session_state.tg_chat_id = st.text_input("Chat ID", value=st.session_state.tg_chat_id)
    
    col_a, col_b = st.columns(2)
    st.session_state.tg_threshold = col_a.number_input("觸發門檻 (%)", min_value=0.1, value=float(st.session_state.tg_threshold))
    st.session_state.tg_reset = col_b.number_input("重置門檻 (%)", min_value=0.1, value=float(st.session_state.tg_reset))

    if st.button("💾 儲存所有設定"):
        save_tg_config()
        st.success("已儲存")

# ── 主畫面其餘部分 (保持原有邏輯 fetch_and_analyze 並渲染卡片) ──
# ... (此處代碼接續您原本的股票清單渲染邏輯)
