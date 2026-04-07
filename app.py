import streamlit as st
import akshare as ak
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
    t = n.time()
    wday = n.weekday()
    if wday >= 5: return True
    if t >= AFTERHOURS_START: return True
    if t < MARKET_OPEN: return True
    return False

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

def load_user_stocks(bid: str) -> list:
    path = os.path.join(USER_DATA_DIR, safe_bid(bid) + ".json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list): return data
        except: pass
    return list(DEFAULT_STOCKS)

def save_user_stocks(bid: str, stocks: list):
    with open(os.path.join(USER_DATA_DIR, safe_bid(bid) + ".json"), "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)

def load_alert_state(bid: str) -> dict:
    path = os.path.join(ALERT_DIR, safe_bid(bid) + "_alert.json")
    today = today_str()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today: return data
        except: pass
    return {"date": today, "states": {}}

def save_alert_state(bid: str, state: dict):
    with open(os.path.join(ALERT_DIR, safe_bid(bid) + "_alert.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ===========================================================================
# --- 2. AKShare 即時報價引擎 (替代 FinMind TaiwanStockQuote) ---
# ===========================================================================
@st.cache_data(ttl=60)
def get_ak_quote(stock_id: str) -> dict:
    """使用 AKShare 抓取新浪即時報價"""
    try:
        df = ak.stock_hk_gj_tw_sina(symbol=stock_id)
        if not df.empty:
            row = df.iloc[0]
            return {
                "price": float(row['last']),
                "pct": float(row['pct_change']),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "source": "AKShare (即時)"
            }
    except: pass
    return {}

# ===========================================================================
# --- 3. 核心運算與指標 (yfinance 歷史 + AKShare 縫合) ---
# ===========================================================================
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
    # 過濾掉今天，只留歷史
    df = df[df.index < pd.Timestamp(today)]
    cache[stock_id] = {"df": df, "cached_date": today}
    return df.copy()

def stitch_with_ak(hist_df: pd.DataFrame, stock_id: str) -> tuple:
    """用 AKShare 即時報價縫合今日棒"""
    quote = get_ak_quote(stock_id)
    if not quote or not is_market_open():
        return hist_df, "🗂 yfinance 歷史"

    today = pd.Timestamp(today_str())
    today_row = pd.Series({
        "Open": quote["open"], "High": quote["high"],
        "Low": quote["low"], "Close": quote["price"], "Volume": 0.0
    }, name=today)

    merged = pd.concat([hist_df, pd.DataFrame([today_row])])
    return merged[~merged.index.duplicated(keep="last")].sort_index(), "📡 AKShare 即時縫合"

def calc_indicators(df: pd.DataFrame):
    if len(df) < 30: return None
    close = pd.Series(df["Close"].values.flatten(), index=df.index).astype(float)
    high = pd.Series(df["High"].values.flatten(), index=df.index).astype(float)
    low = pd.Series(df["Low"].values.flatten(), index=df.index).astype(float)
    try:
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
    except: return None

@st.cache_data(ttl=60)
def fetch_and_analyze(stock_id: str):
    hist_df = get_history_cached(stock_id)
    if hist_df.empty: return None
    df, source = stitch_with_ak(hist_df, stock_id)
    df = calc_indicators(df)
    if df is None: return None

    last, prev = df.iloc[-1], df.iloc[-2]
    score, details = 0, []
    
    if last["MA5"] > last["MA10"] > last["MA20"]: details.append("✅ 均線多頭排列"); score += 1
    if prev["K"] <= prev["D"] and last["K"] > last["D"] and (last["K"] - last["D"]) >= 1.0:
        avg = (last["K"] + last["D"]) / 2
        if avg < 80: details.append("✅ KD 金叉"); score += 1
    if last["MACD_diff"] > 0: details.append("✅ MACD 柱狀體轉正"); score += 1
    if last["RSI"] > 50: details.append("✅ RSI 強勢區"); score += 1
    if last["Close"] > last["BBM"]: details.append("✅ 站穩月線(MA20)"); score += 1

    dm = {5: ("S (極強)", "🔥 續抱/加碼", "red"), 4: ("A (強勢)", "🚀 偏多持股", "orange"),
          3: ("B (轉強)", "📈 少量試單", "green"), 2: ("C (盤整)", "⚖️ 暫時觀望", "blue"),
          1: ("D (弱勢)", "📉 減碼避險", "gray"), 0: ("E (極弱)", "🚫 觀望不進場", "black")}
    grade, action, color = dm[score]

    quote = get_ak_quote(stock_id)
    pct = quote.get("pct", ((last["Close"]-prev["Close"])/prev["Close"]*100)) if quote else ((last["Close"]-prev["Close"])/prev["Close"]*100)
    
    return {
        "price": last["Close"], "pct": pct, "grade": grade, "action": action, 
        "color": color, "details": details, "k": last["K"], "d": last["D"], 
        "source": source, "hist_df": hist_df
    }

# ===========================================================================
# --- 4. 通知與動能分析 (FinMind 僅用於觸發時的深度分析) ---
# ===========================================================================
def send_telegram(msg: str):
    token, cid = st.session_state.tg_token, st.session_state.tg_chat_id
    if token and cid:
        try: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", 
                           json={"chat_id": cid, "text": msg, "parse_mode": "HTML"}, timeout=5)
        except: pass

def fetch_momentum_finmind(stock_id: str, pct: float, threshold: float):
    """觸發瞬間才呼叫 FinMind 深度動能"""
    try:
        dl = DataLoader()
        if st.session_state.finmind_token: dl.login_by_token(st.session_state.finmind_token)
        df = dl.taiwan_stock_minute(stock_id=stock_id, start_date=today_str(), end_date=today_str())
        if df.empty: return ""
        recent = df.tail(6)
        cur_v, avg_v = recent.iloc[-1]['volume'], recent.iloc[:-1]['volume'].mean()
        ratio = cur_v / avg_v if avg_v > 0 else 0
        label = "🚀 帶量突破" if pct >= threshold and ratio >= 1.5 else "💣 帶量殺盤" if pct <= -threshold and ratio >= 1.5 else "🔍 量能正常"
        return f"\n當前量：{int(cur_v)} 張 | 量能比：{ratio:.1f}倍\n短線意涵：{label}"
    except: return ""

# ===========================================================================
# --- 5. UI 介面與主循環 ---
# ===========================================================================
st.set_page_config(page_title="台股 AI 決策系統 V8.0 (AKShare)", layout="centered")

# --- CSS 樣式省略 (保持您原本的美化樣式) ---
st.markdown("<style>...</style>", unsafe_allow_html=True) # 此處建議貼回您原本強大的 CSS 部分

if "initialized" not in st.session_state:
    st.session_state.update({
        "tg_token": "", "tg_chat_id": "", "tg_threshold": 3.0, "tg_reset": 1.0,
        "finmind_token": "", "initialized": True, "hist_cache": {}, "my_stocks": []
    })

get_browser_id_component()
bid = st.query_params.get("bid", "")
if not bid: st.stop()

if st.session_state.get("last_bid") != bid:
    st.session_state.my_stocks = load_user_stocks(bid)
    st.session_state.last_bid = bid

st.title("🤖 台股 AI 技術分級決策 (AKShare)")

# 新增股票 UI
with st.expander("🔍 新增自選股票"):
    c1, c2, c3 = st.columns([2, 3, 1.2])
    nid, nname = c1.text_input("代號"), c2.text_input("名稱")
    if c3.button("➕ 新增") and nid and nname:
        st.session_state.my_stocks.append({"id": nid, "name": nname})
        save_user_stocks(bid, st.session_state.my_stocks)
        st.rerun()

# 股票清單顯示
for idx, stock in enumerate(st.session_state.my_stocks):
    res = fetch_and_analyze(stock["id"])
    if res:
        # --- 通知邏輯 (整合 AKShare 觸發) ---
        alert_state = load_alert_state(bid)
        s_state = alert_state["states"].setdefault(stock["id"], {"alerted": False})
        
        status_label = "⚪ 監控中"
        if abs(res["pct"]) >= st.session_state.tg_threshold and not s_state["alerted"]:
            mom_txt = fetch_momentum_finmind(stock["id"], res["pct"], st.session_state.tg_threshold)
            msg = f"🔔 <b>【價格異動】</b>\n標的：{stock['name']}({stock['id']})\n股價：{res['price']}\n漲跌：{res['pct']:.2f}%{mom_txt}"
            send_telegram(msg)
            s_state.update({"alerted": True, "time": now_tw().strftime("%H:%M")})
            save_alert_state(bid, alert_state)
        
        if s_state["alerted"]:
            if abs(res["pct"]) <= st.session_state.tg_reset:
                s_state["alerted"] = False
                save_alert_state(bid, alert_state)
            else: status_label = f"✅ 已通知 ({s_state.get('time')})"

        # --- HTML 卡片渲染 (保持您原本的格式) ---
        st.info(f"{stock['name']} ({stock['id']}) - 股價: {res['price']} ({res['pct']:.2f}%) | 評級: {res['grade']} | {status_label}")
        # (此處建議嵌入您原本的 card_html 程式碼塊以維持美觀)

# 自動重新整理
if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
