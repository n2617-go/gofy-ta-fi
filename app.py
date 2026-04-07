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

def load_config():
    if os.path.exists(TG_SAVE_FILE):
        try:
            with open(TG_SAVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"finmind_token": "", "tg_token": "", "tg_chat_id": ""}

# ===========================================================================
# --- 2. 混合抓取邏輯 (FinMind 單一快照 + yfinance 備援) ---
# ===========================================================================
def fetch_single_quote(sid: str, token: str):
    """
    這是一個強化的單一股票抓取器。
    它會先試 FinMind 單點快照，失敗再改用 yfinance。
    """
    # --- 1. 嘗試 FinMind 單一快照 (較即時) ---
    if token and len(token) > 5:
        try:
            fm_url = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"
            params = {"token": token, "stock_id": sid}
            resp = requests.get(fm_url, params=params, timeout=5)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    row = data[0]
                    return {
                        "price": float(row.get("close", 0)),
                        "pct": float(row.get("change_rate", 0)),
                        "source": "FinMind 單一快照 (即時)"
                    }
        except:
            pass

    # --- 2. 嘗試 yfinance 備援 (可能延遲) ---
    try:
        # 遍歷上市與上櫃字尾
        for suffix in [".TW", ".TWO"]:
            ticker = yf.Ticker(f"{sid}{suffix}")
            fast = ticker.fast_info
            if fast.last_price is not None and fast.last_price > 0:
                p = fast.last_price
                pc = fast.previous_close
                pct = round(((p - pc) / pc) * 100, 2)
                return {
                    "price": round(p, 2),
                    "pct": pct,
                    "source": f"yfinance ({'延遲' if suffix=='.TW' else '即時'})"
                }
    except:
        pass
        
    return None

# ===========================================================================
# --- 3. UI 渲染 ---
# ===========================================================================
st.set_page_config(page_title="台股決策系統-終極穩定版", layout="centered")

# 初始化 Session State
if "initialized" not in st.session_state:
    cfg = load_config()
    st.session_state.update({**cfg, "initialized": True, "my_stocks": []})

# 瀏覽器 ID 處理
browser_id = st.query_params.get("bid", "")
if browser_id and st.session_state.get("last_bid") != browser_id:
    st.session_state.my_stocks = load_user_stocks(browser_id)
    st.session_state.last_bid = browser_id

get_browser_id_component()
if not browser_id:
    st.info("正在初始化環境，請稍候...")
    st.stop()

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ 系統設定")
    new_token = st.text_input("FinMind Token", value=st.session_state.finmind_token, type="password")
    if st.button("儲存並套用"):
        st.session_state.finmind_token = new_token.strip()
        cfg = load_config()
        cfg["finmind_token"] = st.session_state.finmind_token
        with open(TG_SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        st.rerun()
    st.divider()
    st.caption("註：目前優先使用『單一股票快照』以避開權限限制。")

st.title("🤖 台股決策監控系統")

# --- 新增股票區 ---
with st.expander("➕ 新增關注股票", expanded=False):
    c1, c2, c3 = st.columns([2, 2, 1])
    n_id = c1.text_input("代號", placeholder="例如: 2330")
    n_name = c2.text_input("名稱", placeholder="例如: 台積電")
    if c3.button("新增", use_container_width=True):
        if n_id and n_name:
            if not any(s['id'] == n_id for s in st.session_state.my_stocks):
                st.session_state.my_stocks.append({"id": n_id, "name": n_name})
                save_user_stocks(browser_id, st.session_state.my_stocks)
                st.rerun()

# --- 清單顯示區 ---
st.subheader("📋 監控清單")
if not st.session_state.my_stocks:
    st.write("目前無追蹤股票，請點選上方展開新增。")

# 遍歷股票並抓取
for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    
    # 抓取數據 (這裡會觸發剛才寫的優先級邏輯)
    q = fetch_single_quote(sid, st.session_state.finmind_token)
    
    with st.container(border=True):
        if q:
            price, pct, src = q["price"], q["pct"], q["source"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            arr = "▲" if pct > 0 else "▼" if pct < 0 else "─"
            
            c_info, c_price, c_ctrl = st.columns([3, 3, 2])
            with c_info:
                st.markdown(f"#### {sname}")
                st.caption(f"`{sid}` | {src}")
            with c_price:
                st.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{price}</h2>", unsafe_allow_html=True)
                st.markdown(f"<p style='color:{color}; text-align:right; margin:0;'>{arr} {abs(pct)}%</p>", unsafe_allow_html=True)
            with c_ctrl:
                if st.button("🗑️", key=f"del_{sid}", use_container_width=True):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
                
                # 排序功能
                b1, b2 = st.columns(2)
                if b1.button("↑", key=f"up_{sid}", use_container_width=True) and idx > 0:
                    st.session_state.my_stocks[idx], st.session_state.my_stocks[idx-1] = st.session_state.my_stocks[idx-1], st.session_state.my_stocks[idx]
                    save_user_stocks(browser_id, st.session_state.my_stocks)
