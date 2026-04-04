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
AFTERHOURS_START = dt_time(14, 0)   # 盤後意涵分析起始時間
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

def is_after_hours() -> bool:
    n = now_tw()
    if n.weekday() >= 5: return False
    return n.time() >= AFTERHOURS_START

def today_str() -> str:
    return now_tw().strftime("%Y-%m-%d")

# ===========================================================================
# --- 1. 資料存取與使用者管理 ---
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
    with open(user_file(bid), "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)

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
        except: pass
    return {"date": today, "states": {}}

def save_alert_state(bid: str, state: dict):
    with open(alert_state_file(bid), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ===========================================================================
# --- 2. 指標計算與 API 工具 (修正 TypeError) ---
# ===========================================================================
def calc_indicators(df: pd.DataFrame):
    if df is None or len(df) < 30: return None
    df = df.copy()
    # 確保資料為 1D Series 避免維度錯誤
    close = pd.Series(df["Close"].values.flatten(), index=df.index).astype(float)
    high = pd.Series(df["High"].values.flatten(), index=df.index).astype(float)
    low = pd.Series(df["Low"].values.flatten(), index=df.index).astype(float)

    try:
        # 新版 ta (window)
        df["MA5"] = SMAIndicator(close, window=5).sma_indicator()
        df["MA10"] = SMAIndicator(close, window=10).sma_indicator()
        df["MA20"] = SMAIndicator(close, window=20).sma_indicator()
        stoch = StochasticOscillator(high, low, close, window=9)
        df["K"], df["D"] = stoch.stoch(), stoch.stoch_signal()
        df["MACD_diff"] = MACD(close, window_slow=26, window_fast=12, window_sign=9).macd_diff()
        df["RSI"] = RSIIndicator(close, window=14).rsi()
        df["BBM"] = BollingerBands(close, window=20).bollinger_mavg()
    except TypeError:
        # 舊版 ta (n)
        df["MA5"] = SMAIndicator(close, n=5).sma_indicator()
        df["MA10"] = SMAIndicator(close, n=10).sma_indicator()
        df["MA20"] = SMAIndicator(close, n=20).sma_indicator()
        stoch = StochasticOscillator(high, low, close, n=9)
        df["K"], df["D"] = stoch.stoch(), stoch.stoch_signal()
        df["MACD_diff"] = MACD(close, n_slow=26, n_fast=12, n_sign=9).macd_diff()
        df["RSI"] = RSIIndicator(close, n=14).rsi()
        df["BBM"] = BollingerBands(close, n=20).bollinger_mavg()
    return df

@st.cache_data(ttl=60)
def fetch_all_quotes() -> dict:
    try:
        dl = DataLoader()
        if st.session_state.get("finmind_token"):
            dl.login_by_token(api_token=st.session_state.finmind_token)
        df = dl.taiwan_stock_quote(stock_id="")
        if df is None or df.empty: return {}
        return {str(row["stock_id"]): {"price": float(row.get("close", 0)), "pct": float(row.get("change_rate", 0))} for _, row in df.iterrows()}
    except: return {}

def get_history_cached(stock_id: str):
    cache = st.session_state.hist_cache
    today = today_str()
    if stock_id in cache and cache[stock_id]["date"] == today:
        return cache[stock_id]["df"]
    df = pd.DataFrame()
    for s in [".TW", ".TWO"]:
        try:
            tmp = yf.download(stock_id+s, period="6mo", progress=False)
            if not tmp.empty: df = tmp; break
        except: continue
    if not df.empty:
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df = df.astype(float).ffill()
        cache[stock_id] = {"df": df, "date": today}
    return df

# ===========================================================================
# --- 3. 核心監控與分析邏輯 ---
# ===========================================================================
def send_telegram(token, chat_id, msg):
    if not token or not chat_id: return
    try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                       json={"chat_id":chat_id, "text":msg, "parse_mode":"HTML"}, timeout=5)
    except: pass

def get_momentum_analysis(stock_id, pct, threshold):
    try:
        dl = DataLoader()
        if st.session_state.get("finmind_token"): dl.login_by_token(api_token=st.session_state.finmind_token)
        df = dl.taiwan_stock_minute(stock_id=stock_id, start_date=today_str())
        if df is None or len(df) < 2: return ""
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
        cur_v = df.iloc[-1]['volume']
        avg_v = df.iloc[-6:-1]['volume'].mean() if len(df) >= 6 else df['volume'].mean()
        ratio = cur_v / avg_v if avg_v > 0 else 0
        
        if pct >= threshold:
            return "🚀 帶量突破" if ratio >= 1.5 else "⚠️ 虛假拉抬 (量縮)"
        if pct <= -threshold:
            return "💣 帶量殺盤" if ratio >= 1.5 else "🔍 洗盤觀察 (量縮)"
        return ""
    except: return ""

def run_afterhours_analysis(bid, stock, pct, hist_df, threshold):
    if hist_df.empty or not is_after_hours(): return ""
    
    # 檢查是否已存有今日盤後意涵
    alert_state = load_alert_state(bid)
    stock_state = alert_state["states"].get(stock["id"], {})
    if stock_state.get("ah_impl"): return stock_state["ah_impl"]

    try:
        mav5 = float(hist_df["Volume"].iloc[-5:].mean())
        dl = DataLoader()
        if st.session_state.get("finmind_token"): dl.login_by_token(api_token=st.session_state.finmind_token)
        df_today = dl.taiwan_stock_daily(stock_id=stock["id"], start_date=today_str())
        if df_today is None or df_today.empty: return ""
        
        today_vol = float(df_today.iloc[-1]["volume"])
        ratio = today_vol / mav5
        impl = ""
        if pct >= threshold:
            impl = "📈 盤後意涵：量增上漲，可考慮留倉" if ratio > 1.1 else "⚠️ 盤後意涵：量縮上漲，不宜追高"
        elif pct <= -threshold:
            impl = "💣 盤後意涵：趨勢轉弱，建議避開" if ratio > 1.1 else "🔍 盤後意涵：量縮下跌，可尋買點"
        
        if impl:
            # 存入狀態並發送通知
            stock_state["ah_impl"] = impl
            alert_state["states"][stock["id"]] = stock_state
            save_alert_state(bid, alert_state)
            send_telegram(st.session_state.tg_token, st.session_state.tg_chat_id, 
                          f"📊 <b>盤後意涵分析</b>\n標的：{stock['name']}\n{impl}")
        return impl
    except: return ""

# ===========================================================================
# --- 4. 主程式介面 ---
# ===========================================================================
st.set_page_config(page_title="台股決策系統", layout="wide")

if "initialized" not in st.session_state:
    cfg = load_tg_config()
    st.session_state.update({**cfg, "initialized": True, "hist_cache": {}})

# 獲取瀏覽器 ID
browser_id = st.query_params.get("bid", "")
if not browser_id:
    get_browser_id_component()
    st.stop()

my_stocks = load_user_stocks(browser_id)

with st.sidebar:
    st.title("⚙️ 控制面板")
    st.session_state.finmind_token = st.text_input("FinMind Token", value=st.session_state.finmind_token, type="password")
    st.session_state.tg_token = st.text_input("TG Bot Token", value=st.session_state.tg_token, type="password")
    st.session_state.tg_chat_id = st.text_input("TG Chat ID", value=st.session_state.tg_chat_id)
    st.session_state.tg_threshold = st.number_input("觸發門檻 (%)", value=float(st.session_state.tg_threshold), step=0.1)
    st.session_state.tg_reset = st.number_input("重置門檻 (%)", value=float(st.session_state.tg_reset), step=0.1)
    if st.button("💾 儲存設定"):
        save_tg_config()
        st.success("設定已儲存")
    
    new_id = st.text_input("➕ 新增股票代號 (如: 2330)")
    new_name = st.text_input("股票名稱")
    if st.button("確認新增") and new_id and new_name:
        my_stocks.append({"id": new_id, "name": new_name})
        save_user_stocks(browser_id, my_stocks)
        st.rerun()

st.title("📈 我的監控清單")
quotes = fetch_all_quotes()

for idx, stock in enumerate(my_stocks):
    q = quotes.get(stock["id"])
    hist = get_history_cached(stock["id"])
    
    with st.container(border=True):
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.subheader(f"{stock['name']} ({stock['id']})")
            if q:
                pct = q["pct"]
                price = q["price"]
                # 盤中監控邏輯
                alert_state = load_alert_state(browser_id)
                s = alert_state["states"].get(stock["id"], {"alerted": False})
                
                # 檢查是否觸發
                if not s.get("alerted") and abs(pct) >= st.session_state.tg_threshold:
                    mom_label = get_momentum_analysis(stock["id"], pct, st.session_state.tg_threshold)
                    msg = f"🔔 <b>價格觸發</b>\n標的：{stock['name']}\n價格：{price}\n漲跌：{pct:+.2f}%\n{mom_label}"
                    send_telegram(st.session_state.tg_token, st.session_state.tg_chat_id, msg)
                    s.update({"alerted": True, "time": now_tw().strftime("%H:%M")})
                    alert_state["states"][stock["id"]] = s
                    save_alert_state(browser_id, alert_state)
                
                # 檢查重置
                elif s.get("alerted") and abs(pct) <= st.session_state.tg_reset:
                    s["alerted"] = False
                    alert_state["states"][stock["id"]] = s
                    save_alert_state(browser_id, alert_state)

                st.write(f"當前價格: **{price}** ({pct:+.2f}%)")
                
                # 盤後意涵顯示區
                if is_after_hours() and abs(pct) >= st.session_state.tg_threshold:
                    ah_label = run_afterhours_analysis(browser_id, stock, pct, hist, st.session_state.tg_threshold)
                    if ah_label: st.info(ah_label)
            else:
                st.error("暫無即時報價")

        with col2:
            # 顯示技術指標摘要
            df_idx = calc_indicators(hist)
            if df_idx is not None:
                last = df_idx.iloc[-1]
                st.caption(f"RSI: {last['RSI']:.1f} | K: {last['K']:.1f}")

        with col3:
            if st.button("🗑️ 刪除", key=f"del_{stock['id']}"):
                my_stocks.pop(idx)
                save_user_stocks(browser_id, my_stocks)
                st.rerun()

# 交易時段自動刷新 (1分鐘)
if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
