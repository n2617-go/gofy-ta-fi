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
# --- 1. 使用者管理與初始化 ---
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
    return {"finmind_token": ""}

# 初始化 Session State (必須放在最前面)
if "api_logs" not in st.session_state:
    st.session_state.api_logs = []
if "initialized" not in st.session_state:
    cfg = load_config()
    st.session_state.update({**cfg, "initialized": True, "my_stocks": []})

# ===========================================================================
# --- 2. 診斷式數據抓取邏輯 ---
# ===========================================================================
def add_log(msg):
    time_str = now_tw().strftime("%H:%M:%S")
    st.session_state.api_logs.insert(0, f"[{time_str}] {msg}")
    st.session_state.api_logs = st.session_state.api_logs[:15] # 保留15筆

def fetch_stock_with_diag(sid: str, token: str):
    """
    1. 嘗試 FinMind 單一快照
    2. 失敗則嘗試 yfinance
    """
    clean_token = token.strip() if token else ""
    
    # --- Step A: FinMind ---
    if clean_token:
        try:
            url = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"
            params = {"token": clean_token, "stock_id": sid}
            resp = requests.get(url, params=params, timeout=5)
            
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    add_log(f"✅ FinMind 成功: {sid}")
                    row = data[0]
                    return {
                        "price": float(row.get("close", 0)),
                        "pct": float(row.get("change_rate", 0)),
                        "source": "FinMind (即時)"
                    }
                else:
                    add_log(f"⚠️ FinMind 空值: {sid} (查無資料)")
            else:
                # 這裡會抓到您提到的 400 錯誤與具體原因
                err_msg = resp.json().get("msg", "未知錯誤")
                add_log(f"❌ FinMind 失敗: {sid} ({resp.status_code}: {err_msg})")
        except Exception as e:
            add_log(f"📡 FinMind 連線異常: {sid} ({str(e)})")
    
    # --- Step B: yfinance ---
    add_log(f"🔄 啟動 yfinance 備援: {sid}")
    try:
        for suffix in [".TW", ".TWO"]:
            t = yf.Ticker(f"{sid}{suffix}")
            fast = t.fast_info
            if fast.last_price and fast.last_price > 0:
                p = fast.last_price
                pc = fast.previous_close
                return {
                    "price": round(p, 2),
                    "pct": round(((p - pc) / pc) * 100, 2),
                    "source": f"yfinance ({'延遲' if suffix=='.TW' else '即時'})"
                }
    except Exception as e:
        add_log(f"❌ yfinance 也失敗: {sid} ({str(e)})")
        
    return None

# ===========================================================================
# --- 3. UI 渲染 ---
# ===========================================================================
st.set_page_config(page_title="台股監控-日誌加強版", layout="centered")

# 瀏覽器 ID 處理
browser_id = st.query_params.get("bid", "")
if browser_id and st.session_state.get("last_bid") != browser_id:
    st.session_state.my_stocks = load_user_stocks(browser_id)
    st.session_state.last_bid = browser_id

get_browser_id_component()
if not browser_id: st.stop()

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ 系統設定")
    fm_token_input = st.text_input("FinMind Token", value=st.session_state.finmind_token, type="password")
    if st.button("儲存並重新連線"):
        st.session_state.finmind_token = fm_token_input.strip()
        with open(TG_SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump({"finmind_token": st.session_state.finmind_token}, f)
        st.session_state.api_logs = [] # 清空日誌重新開始
        st.rerun()
    st.divider()
    st.write("目前狀態：", "🟢 盤中監控中" if is_market_open() else "⚪ 非交易時段")

st.title("🤖 台股監控與 API 診斷")

# --- 新增股票 ---
with st.expander("➕ 新增股票到清單"):
    c1, c2, c3 = st.columns([2, 2, 1])
    n_id = c1.text_input("代號", placeholder="2330")
    n_name = c2.text_input("名稱", placeholder="台積電")
    if c3.button("新增"):
        if n_id and n_name:
            st.session_state.my_stocks.append({"id": n_id, "name": n_name})
            save_user_stocks(browser_id, st.session_state.my_stocks)
            st.rerun()

# --- 股票列表 ---
st.subheader("📋 即時報價清單")
for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    
    # 執行抓取
    q = fetch_stock_with_diag(sid, st.session_state.finmind_token)
    
    with st.container(border=True):
        if q:
            price, pct, src = q["price"], q["pct"], q["source"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            
            c_l, c_r, c_del = st.columns([4, 3, 2])
            with c_l:
                st.markdown(f"#### {sname} `{sid}`")
                st.caption(f"來源: {src}")
            with c_r:
                st.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{price}</h2>", unsafe_allow_html=True)
                st.markdown(f"<p style='color:{color}; text-align:right; margin:0;'>{pct}%</p>", unsafe_allow_html=True)
            with c_del:
                if st.button("🗑️", key=f"del_{sid}", use_container_width=True):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
        else:
            st.error(f"❌ {sname} ({sid}) 無法取得資料，請見下方診斷日誌。")

# ===========================================================================
# --- 4. 診斷日誌區 (放在最下方最明顯) ---
# ===========================================================================
st.divider()
with st.expander("📡 API 診斷日誌 (點擊展開)", expanded=True):
    if not st.session_state.api_logs:
        st.write("尚無連線紀錄...")
    else:
        for log in st.session_state.api_logs:
            # 根據關鍵字著色
            if "成功" in log:
                st.write(f"🟢 {log}")
            elif "失敗" in log or "異常" in log:
                st.write(f"🔴 {log}")
            else:
                st.write(f"⚪ {log}")

# 自動更新
if is_market_open():
    components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
