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

def is_after_hours() -> bool:
    n = now_tw()
    if n.weekday() >= 5: return False
    return n.time() >= AFTERHOURS_START

def today_str() -> str:
    return now_tw().strftime("%Y-%m-%d")

# ===========================================================================
# --- 1. 使用者識別與資料管理 ---
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
# --- 2. 配置與 API 工具 ---
# ===========================================================================
def load_tg_config() -> dict:
    if os.path.exists(TG_SAVE_FILE):
        try:
            with open(TG_SAVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"tg_token":"", "tg_chat_id":"", "tg_threshold":3.0, "tg_reset":1.0, "finmind_token":""}

def save_tg_config():
    with open(TG_SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "tg_token": st.session_state.tg_token,
            "tg_chat_id": st.session_state.tg_chat_id,
            "tg_threshold": st.session_state.tg_threshold,
            "tg_reset": st.session_state.tg_reset,
            "finmind_token": st.session_state.finmind_token,
        }, f, ensure_ascii=False, indent=4)

def get_finmind_loader():
    dl = DataLoader()
    if st.session_state.get("finmind_token"):
        dl.login_by_token(api_token=st.session_state.finmind_token)
    return dl

# ===========================================================================
# --- 3. 報價與技術指標 ---
# ===========================================================================
@st.cache_data(ttl=60)
def fetch_all_quotes() -> dict:
    try:
        dl = get_finmind_loader()
        df = dl.taiwan_stock_quote(stock_id="")
        if df is None or df.empty: return {}
        result = {}
        for _, row in df.iterrows():
            sid = str(row.get("stock_id", ""))
            if sid:
                price = float(row.get("close", row.get("price", 0)))
                result[sid] = {"price": price, "pct": float(row.get("change_rate", 0)), "open": float(row.get("open", 0))}
        return result
    except: return {}

def get_history_cached(stock_id: str) -> pd.DataFrame:
    cache = st.session_state.hist_cache
    today = today_str()
    if stock_id in cache and cache[stock_id]["cached_date"] == today:
        return cache[stock_id]["df"].copy()
    df = pd.DataFrame()
    for suffix in [".TW", ".TWO"]:
        try:
            temp = yf.download(stock_id + suffix, period="6mo", progress=False)
            if not temp.empty:
                df = temp
                break
        except: continue
    if df.empty: return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    df = df.astype(float).ffill()
    df.index = pd.to_datetime(df.index).normalize()
    yesterday = pd.Timestamp(today) - timedelta(days=1)
    df = df[df.index <= yesterday]
    cache[stock_id] = {"df": df, "cached_date": today}
    return df.copy()

def calc_indicators(df: pd.DataFrame):
    if len(df) < 30: return None
    close = pd.Series(df["Close"].values.flatten(), index=df.index).astype(float)
    high = pd.Series(df["High"].values.flatten(), index=df.index).astype(float)
    low = pd.Series(df["Low"].values.flatten(), index=df.index).astype(float)
    df = df.copy()
    df["MA5"] = SMAIndicator(close, window=5).sma_indicator()
    df["MA10"] = SMAIndicator(close, window=10).sma_indicator()
    df["MA20"] = SMAIndicator(close, window=20).sma_indicator()
    stoch = StochasticOscillator(high, low, close, window=9)
    df["K"], df["D"] = stoch.stoch(), stoch.stoch_signal()
    df["MACD_diff"] = MACD(close).macd_diff()
    df["RSI"] = RSIIndicator(close).rsi()
    df["BBM"] = BollingerBands(close).bollinger_mavg()
    return df

# ===========================================================================
# --- 4. 核心分析邏輯 ---
# ===========================================================================
def fetch_momentum_analysis(stock_id: str, pct: float, tg_threshold: float) -> dict:
    try:
        dl = get_finmind_loader()
        today = today_str()
        df = dl.taiwan_stock_minute(stock_id=stock_id, start_date=today, end_date=today)
        if df is None or df.empty: return {}
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        recent = df.tail(6)
        if len(recent) < 2: return {}
        cur_v, avg_v = float(recent.iloc[-1]['volume']), float(recent.iloc[:-1]['volume'].mean())
        ratio = cur_v / avg_v if avg_v > 0 else 0.0
        
        label = "➡️ 正常"
        if ratio >= 2.0: label = "🔥 爆量"
        elif ratio >= 1.5: label = "📈 放量"
        elif ratio < 1.0: label = "📉 縮量"

        short_impl = ""
        if pct >= tg_threshold and ratio >= 1.5: short_impl = "🚀 短線意涵：帶量突破"
        elif pct >= tg_threshold and ratio < 1.0: short_impl = "⚠️ 短線意涵：虛假拉抬"
        elif pct <= -tg_threshold and ratio >= 1.5: short_impl = "💣 短線意涵：帶量殺盤"
        elif pct <= -tg_threshold and ratio < 1.0: short_impl = "🔍 短線意涵：洗盤觀察"

        return {"cur_vol": int(cur_v), "avg_vol": int(avg_v), "ratio": round(ratio, 2), "momentum_label": label, "short_impl": short_impl}
    except: return {}

def run_afterhours_analysis(bid: str, stock: dict, pct: float, hist_df: pd.DataFrame, tg_threshold: float):
    # 1. 取得 5MAV (歷史)
    if hist_df.empty: return ""
    mav5 = float(hist_df["Volume"].iloc[-5:].mean()) if len(hist_df) >= 5 else 0
    if mav5 <= 0: return ""

    # 2. 取得今日收盤總量
    try:
        dl = get_finmind_loader()
        today = today_str()
        df = dl.taiwan_stock_daily(stock_id=stock["id"], start_date=today, end_date=today)
        if df is None or df.empty: return ""
        close_vol = float(df.iloc[-1]["volume"])
    except: return ""

    # 3. 判斷意涵
    ratio = close_vol / mav5
    impl = ""
    if pct >= tg_threshold and ratio > 1.1: impl = "📈 盤後意涵：量增上漲，可考慮留倉"
    elif pct >= tg_threshold and ratio < 0.9: impl = "⚠️ 盤後意涵：量縮上漲，不宜追高"
    elif pct <= -tg_threshold and ratio > 1.1: impl = "💣 盤後意涵：趨勢轉弱，建議避開"
    elif pct <= -tg_threshold and ratio < 0.9: impl = "🔍 盤後意涵：量縮下跌，可尋買點"
    
    # 4. 存入狀態並發送 Telegram
    if impl:
        alert_state = load_alert_state(bid)
        s = alert_state["states"].setdefault(stock["id"], {})
        if not s.get("ah_sent"):
            send_telegram(st.session_state.tg_token, st.session_state.tg_chat_id, f"📊 <b>盤後意涵分析</b>\n標的：{stock['name']}\n{impl}")
            s["ah_sent"] = True
            s["ah_label"] = impl
            save_alert_state(bid, alert_state)
    return impl

# ===========================================================================
# --- 5. 通知與發送 ---
# ===========================================================================
def send_telegram(token, chat_id, msg):
    if not token or not chat_id: return
    try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id":chat_id, "text":msg, "parse_mode":"HTML"}, timeout=5)
    except: pass

def check_and_notify(bid, stock, pct, res, tg_token, tg_chat_id, tg_threshold, tg_reset):
    alert_state = load_alert_state(bid)
    s = alert_state["states"].setdefault(stock["id"], {"alerted": False, "momentum": {}})
    
    abs_pct = abs(pct)
    if s["alerted"]:
        if abs_pct <= tg_reset:
            s["alerted"] = False
            save_alert_state(bid, alert_state)
            return "🔓 已重置"
        return f"🔒 鎖定中 ({s.get('alerted_at')})"
    else:
        if abs_pct >= tg_threshold:
            mom = fetch_momentum_analysis(stock["id"], pct, tg_threshold)
            s.update({"alerted": True, "alerted_at": now_tw().strftime("%H:%M"), "momentum": mom, "ever_triggered": True})
            msg = f"🔔 <b>價格異動</b>\n標的：{stock['name']}\n價格：{res['price']}\n漲跌：{pct:+.2f}%\n{mom.get('short_impl','')}"
            send_telegram(tg_token, tg_chat_id, msg)
            save_alert_state(bid, alert_state)
            return f"✅ 已通知 ({s['alerted_at']})"
    return f"⚪ 監控中 ({pct:+.2f}%)"

# ===========================================================================
# --- 6. 介面初始化 ---
# ===========================================================================
st.set_page_config(page_title="台股決策系統 V7.6", layout="centered")
if "initialized" not in st.session_state:
    cfg = load_tg_config()
    st.session_state.update({**cfg, "initialized":True, "hist_cache":{}, "my_stocks":[]})

browser_id = st.query_params.get("bid", "")
if not browser_id:
    get_browser_id_component()
    st.stop()
st.session_state.my_stocks = load_user_stocks(browser_id)

# ===========================================================================
# --- 7. 股票清單顯示 ---
# ===========================================================================
st.title("🤖 台股 AI 技術分級決策支援")

# 側邊欄設定 (略，保持原有 UI)
with st.sidebar:
    st.header("⚙️ 設定")
    st.session_state.finmind_token = st.text_input("FinMind Token", value=st.session_state.finmind_token, type="password")
    st.session_state.tg_token = st.text_input("TG Bot Token", value=st.session_state.tg_token, type="password")
    st.session_state.tg_chat_id = st.text_input("TG Chat ID", value=st.session_state.tg_chat_id)
    st.session_state.tg_threshold = st.number_input("觸發門檻", value=float(st.session_state.tg_threshold))
    st.session_state.tg_reset = st.number_input("重置門檻", value=float(st.session_state.tg_reset))
    if st.button("💾 儲存設定"): save_tg_config(); st.success("儲存成功")

# 股票卡片
for idx, stock in enumerate(st.session_state.my_stocks):
    hist = get_history_cached(stock["id"])
    quotes = fetch_all_quotes()
    q = quotes.get(stock["id"], {"price":0, "pct":0})
    
    # 計算技術指標 (縫合數據)
    df = calc_indicators(hist) # 此處簡化，實際應用可加入縫合邏輯
    
    with st.container(border=True):
        res = {"price": q["price"], "pct": q["pct"]}
        st.subheader(f"{stock['name']} ({stock['id']})")
        
        # 盤中通知
        label = check_and_notify(browser_id, stock, q["pct"], res, st.session_state.tg_token, st.session_state.tg_chat_id, st.session_state.tg_threshold, st.session_state.tg_reset)
        st.caption(f"通知狀態：{label}")

        # 盤後意涵 (修正重點)
        if is_after_hours():
            # 只要漲跌幅達標，不論是否曾觸發通知都檢查
            if abs(q["pct"]) >= st.session_state.tg_threshold:
                ah_label = run_afterhours_analysis(browser_id, stock, q["pct"], hist, st.session_state.tg_threshold)
                if ah_label: st.info(ah_label)
        
        if st.button("🗑️ 刪除", key=f"del_{stock['id']}"):
            st.session_state.my_stocks.pop(idx)
            save_user_stocks(browser_id, st.session_state.my_stocks)
            st.rerun()

# 自動重新整理腳本
if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
