
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
    n, t, wday = now_tw(), now_tw().time(), now_tw().weekday()
    if wday >= 5: return True
    if t >= AFTERHOURS_START or t < MARKET_OPEN: return True
    return False

def today_str() -> str:
    return now_tw().strftime("%Y-%m-%d")

# ===========================================================================
# --- 1. 使用者識別與 Local Storage ---
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
                return json.load(f)
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
# --- 2. 數據引擎：AKShare 即時 + yfinance 歷史 ---
# ===========================================================================
@st.cache_data(ttl=60)
def get_ak_quote(stock_id: str) -> dict:
    try:
        df = ak.stock_hk_gj_tw_sina(symbol=stock_id)
        if not df.empty:
            row = df.iloc[0]
            return {
                "price": float(row['last']), "pct": float(row['pct_change']),
                "open": float(row['open']), "high": float(row['high']),
                "low": float(row['low']), "source": "AKShare (即時)"
            }
    except: pass
    return {}

def get_history_cached(stock_id: str) -> pd.DataFrame:
    cache, today = st.session_state.hist_cache, today_str()
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
    df = df[df.index < pd.Timestamp(today)]
    cache[stock_id] = {"df": df, "cached_date": today}
    return df.copy()

def calc_indicators(df: pd.DataFrame):
    if len(df) < 30: return None
    close, high, low = df["Close"], df["High"], df["Low"]
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
    
    quote = get_ak_quote(stock_id)
    df = hist_df.copy()
    source = "🗂 yfinance 歷史"
    
    if quote and is_market_open():
        today_row = pd.Series({"Open": quote["open"], "High": quote["high"], "Low": quote["low"], "Close": quote["price"], "Volume": 0.0}, name=pd.Timestamp(today_str()))
        df = pd.concat([df, pd.DataFrame([today_row])])
        df = df[~df.index.duplicated(keep="last")].sort_index()
        source = "📡 AKShare 即時縫合"
        
    df = calc_indicators(df)
    if df is None: return None
    last, prev = df.iloc[-1], df.iloc[-2]
    
    score, details = 0, []
    if last["MA5"] > last["MA10"] > last["MA20"]: details.append("✅ 均線多頭排列"); score += 1
    if prev["K"] <= prev["D"] and last["K"] > last["D"] and (last["K"]-last["D"]) >= 1.0:
        if (last["K"]+last["D"])/2 < 80: details.append("✅ KD 金叉"); score += 1
    if last["MACD_diff"] > 0: details.append("✅ MACD 柱狀體轉正"); score += 1
    if last["RSI"] > 50: details.append("✅ RSI 強勢區"); score += 1
    if last["Close"] > last["BBM"]: details.append("✅ 站穩月線(MA20)"); score += 1

    dm = {5:("S (極強)","🔥 續抱/加碼","red"), 4:("A (強勢)","🚀 偏多持股","orange"), 3:("B (轉強)","📈 少量試單","green"), 2:("C (盤整)","⚖️ 暫時觀望","blue"), 1:("D (弱勢)","📉 減碼避險","gray"), 0:("E (極弱)","🚫 觀望不進場","black")}
    grade, action, color = dm[score]
    
    return {"price": last["Close"], "pct": quote.get("pct", (last["Close"]-prev["Close"])/prev["Close"]*100), "grade": grade, "action": action, "color": color, "details": details, "k": last["K"], "d": last["D"], "source": source, "hist_df": hist_df}

# ===========================================================================
# --- 3. UI 樣式與通知 ---
# ===========================================================================
st.set_page_config(page_title="台股 AI 決策系統 V8.5", layout="centered")

st.markdown("""
<style>
.stock-card { background: #1e1e2e; border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 15px; margin-bottom: 10px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.card-price { font-size: 1.8rem; font-weight: 800; color: #fff; }
.badge { padding: 4px 10px; border-radius: 20px; font-size: 0.85rem; font-weight: 600; margin-right: 5px; }
.action-red { color: #ff4b4b; font-weight: 700; }
.action-orange { color: #ffa500; font-weight: 700; }
.action-green { color: #00ff7f; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

if "initialized" not in st.session_state:
    st.session_state.update({"tg_token": "", "tg_chat_id": "", "tg_threshold": 3.0, "tg_reset": 1.0, "finmind_token": "", "initialized": True, "hist_cache": {}, "my_stocks": []})

get_browser_id_component()
browser_id = st.query_params.get("bid", "")
if not browser_id: st.stop()

if st.session_state.get("last_bid") != browser_id:
    st.session_state.my_stocks = load_user_stocks(browser_id)
    st.session_state.last_bid = browser_id

# --- 新增股票 ---
with st.container(border=True):
    st.subheader("🔍 新增自選股票")
    c1, c2, c3 = st.columns([2, 3, 1.2])
    n_id, n_name = c1.text_input("代號"), c2.text_input("名稱")
    if c3.button("➕ 新增", use_container_width=True) and n_id and n_name:
        st.session_state.my_stocks.append({"id": n_id, "name": n_name})
        save_user_stocks(browser_id, st.session_state.my_stocks)
        st.rerun()

# --- 股票列表 ---
st.divider()
for idx, stock in enumerate(st.session_state.my_stocks):
    res = fetch_and_analyze(stock["id"])
    if res:
        sid, name = stock["id"], stock["name"]
        pct_color = "#ff4b4b" if res["pct"] > 0 else "#00ff7f" if res["pct"] < 0 else "#ccc"
        
        # 通知邏輯
        alert_state = load_alert_state(browser_id)
        s_alert = alert_state["states"].setdefault(sid, {"alerted": False})
        alert_label = "⚪ 監控中"
        
        if abs(res["pct"]) >= st.session_state.tg_threshold and not s_alert["alerted"]:
            msg = f"🔔 【異動通知】\n標的：{name}({sid})\n股價：{res['price']}\n漲跌：{res['pct']:.2f}%"
            try: requests.post(f"https://api.telegram.org/bot{st.session_state.tg_token}/sendMessage", json={"chat_id": st.session_state.tg_chat_id, "text": msg})
            except: pass
            s_alert.update({"alerted": True, "time": now_tw().strftime("%H:%M")})
            save_alert_state(browser_id, alert_state)
            
        if s_alert["alerted"]:
            if abs(res["pct"]) <= st.session_state.tg_reset:
                s_alert["alerted"] = False
                save_alert_state(browser_id, alert_state)
            else: alert_label = f"✅ 已通知 ({s_alert.get('time')})"

        # HTML 卡片
        st.markdown(f"""
        <div class="stock-card">
            <div class="card-header">
                <div style="font-size:1.2rem; font-weight:700;">{name} <small>({sid})</small></div>
                <div class="card-price" style="color:{pct_color};">{res['price']:.2f} <small style="font-size:1rem;">({res['pct']:+.2f}%)</small></div>
            </div>
            <div style="margin-top:10px;">
                <span class="badge" style="background:rgba(99,179,237,0.2); color:#63b3ed;">{res['grade']}</span>
                <span class="action-{res['color']}">{res['action']}</span>
                <span style="float:right; font-size:0.8rem; opacity:0.5;">{res['source']}</span>
            </div>
            <div style="margin-top:8px; font-size:0.9rem; opacity:0.8;">
                指標：{' '.join(res['details']) if res['details'] else '無'} | KD: K={res['k']:.1f} D={res['d']:.1f}
            </div>
            <div style="margin-top:5px; font-size:0.85rem; color:#a0aec0;">{alert_label}</div>
        </div>
        """, unsafe_allow_html=True)
        
        # 操作按鈕
        b1, b2, b3, _ = st.columns([1,1,1,4])
        if b1.button("🗑️", key=f"del_{sid}"):
            st.session_state.my_stocks.pop(idx)
            save_user_stocks(browser_id, st.session_state.my_stocks)
            st.rerun()
        if b2.button("↑", key=f"up_{sid}") and idx > 0:
            st.session_state.my_stocks[idx], st.session_state.my_stocks[idx-1] = st.session_state.my_stocks[idx-1], st.session_state.my_stocks[idx]
            save_user_stocks(browser_id, st.session_state.my_stocks)
            st.rerun()
        if b3.button("↓", key=f"dn_{sid}") and idx < len(st.session_state.my_stocks)-1:
            st.session_state.my_stocks[idx], st.session_state.my_stocks[idx+1] = st.session_state.my_stocks[idx+1], st.session_state.my_stocks[idx]
            save_user_stocks(browser_id, st.session_state.my_stocks)
            st.rerun()

# 側邊欄設定
with st.sidebar:
    st.header("⚙️ 系統設定")
    st.session_state.tg_token = st.text_input("TG Bot Token", type="password", value=st.session_state.tg_token)
    st.session_state.tg_chat_id = st.text_input("TG Chat ID", value=st.session_state.tg_chat_id)
    st.session_state.tg_threshold = st.number_input("觸發門檻%", value=st.session_state.tg_threshold)
    st.session_state.tg_reset = st.number_input("重置門檻%", value=st.session_state.tg_reset)
    if st.button("💾 儲存設定"): st.success("設定已更新")

# 自動重新整理
if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
