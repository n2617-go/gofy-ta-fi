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

def load_config() -> dict:
    if os.path.exists(TG_SAVE_FILE):
        try:
            with open(TG_SAVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"finmind_token": "", "tg_token": "", "tg_chat_id": ""}

# ===========================================================================
# --- 2. 核心報價抓取（含診斷邏輯） ---
# ===========================================================================
def get_finmind_loader():
    dl = DataLoader()
    token = st.session_state.get("finmind_token", "").strip()
    if token:
        try:
            dl.login_by_token(api_token=token)
            st.session_state["fm_status"] = "✅ Token 登入成功"
        except Exception as e:
            st.session_state["fm_status"] = f"❌ Token 驗證失敗: {str(e)}"
    else:
        st.session_state["fm_status"] = "⚠️ 未輸入 Token (匿名模式)"
    return dl

@st.cache_data(ttl=30)
def fetch_all_quotes_with_diag() -> tuple:
    """回傳 (數據字典, 診斷訊息)"""
    diag = {"status": "未知", "msg": ""}
    try:
        dl = get_finmind_loader()
        df = dl.taiwan_stock_tick_snapshot(stock_id="")
        
        if df is None or df.empty:
            diag["status"] = "快照失敗"
            diag["msg"] = "API 回傳空值 (Empty DataFrame)"
            return {}, diag
            
        data = {str(row["stock_id"]): {
            "price": float(row["close"]), 
            "pct": float(row["change_rate"]), 
            "source": "FinMind 全體快照"
        } for _, row in df.iterrows()}
        
        diag["status"] = "成功"
        return data, diag
    except Exception as e:
        diag["status"] = "系統錯誤"
        diag["msg"] = str(e)
        return {}, diag

def get_fallback_quote(sid: str) -> dict:
    """yfinance 備援"""
    try:
        t = yf.Ticker(f"{sid}.TW")
        info = t.fast_info
        return {
            "price": round(info.last_price, 2),
            "pct": round(((info.last_price - info.previous_close) / info.previous_close) * 100, 2),
            "source": "yfinance 備援"
        }
    except:
        return None

# ===========================================================================
# --- 3. UI 介面 ---
# ===========================================================================
st.set_page_config(page_title="台股監控-診斷版", layout="centered")

# 初始化
if "initialized" not in st.session_state:
    cfg = load_config()
    st.session_state.update({**cfg, "initialized": True, "my_stocks": []})

browser_id = st.query_params.get("bid", "")
if browser_id and st.session_state.get("last_bid") != browser_id:
    st.session_state.my_stocks = load_user_stocks(browser_id)
    st.session_state.last_bid = browser_id

get_browser_id_component()
if not browser_id: st.stop()

# --- Sidebar 診斷面板 ---
with st.sidebar:
    st.header("🔍 系統診斷")
    
    # Token 輸入與確認
    fm_token_input = st.text_input("FinMind Token", value=st.session_state.finmind_token, type="password")
    if st.button("確認並儲存 Token"):
        st.session_state.finmind_token = fm_token_input.strip()
        cfg = load_config()
        cfg["finmind_token"] = st.session_state.finmind_token
        with open(TG_SAVE_FILE, "w", encoding="utf-8") as f: json.dump(cfg, f)
        st.cache_data.clear()
        st.rerun()

    # 顯示目前 Token 狀態
    if "fm_status" in st.session_state:
        st.info(st.session_state["fm_status"])

st.title("🤖 台股決策診斷系統")

# --- 新增股票 ---
with st.expander("➕ 新增關注股票", expanded=False):
    c1, c2, c3 = st.columns([2, 2, 1])
    n_id = c1.text_input("股票代號")
    n_name = c2.text_input("簡稱")
    if c3.button("新增"):
        if n_id and n_name:
            st.session_state.my_stocks.append({"id": n_id, "name": n_name})
            save_user_stocks(browser_id, st.session_state.my_stocks)
            st.rerun()

# --- 抓取與顯示 ---
all_quotes, diag_info = fetch_all_quotes_with_diag()

# 顯示目前的快照抓取狀態
if diag_info["status"] != "成功":
    st.warning(f"⚠️ FinMind 快照不可用：{diag_info['msg']} (切換至備援機制)")
else:
    st.success("✅ FinMind 全體快照連線正常")

for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    
    # 邏輯：先看全體快照，沒有就去 yfinance
    q = all_quotes.get(sid)
    if not q:
        q = get_fallback_quote(sid)
    
    with st.container(border=True):
        if q:
            price, pct, src = q["price"], q["pct"], q["source"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            
            col_l, col_r, col_btn = st.columns([3, 3, 2])
            with col_l:
                st.markdown(f"### {sname}")
                st.caption(f"代號: {sid} | 來源: `{src}`")
            with col_r:
                st.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{price}</h2>", unsafe_allow_html=True)
                st.markdown(f"<p style='color:{color}; text-align:right; margin:0;'>{pct}%</p>", unsafe_allow_html=True)
            with col_btn:
                if st.button("🗑️", key=f"del_{sid}", use_container_width=True):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
        else:
            st.error(f"❌ {sname} ({sid}) 完全抓不到數據")

# 自動更新
if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
