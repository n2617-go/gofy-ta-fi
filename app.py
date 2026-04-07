import streamlit as st
import akshare as ak
import yfinance as yf
import pandas as pd
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
USER_DATA_DIR = "user_data"
LS_KEY = "tw_stock_browser_id"

os.makedirs(USER_DATA_DIR, exist_ok=True)

def now_tw() -> datetime:
    return datetime.now(tw_tz)

# ===========================================================================
# --- 1. 數據抓取邏輯 (AKShare + yfinance) ---
# ===========================================================================
def add_log(msg):
    if "api_logs" not in st.session_state: st.session_state.api_logs = []
    st.session_state.api_logs.insert(0, f"[{now_tw().strftime('%H:%M:%S')}] {msg}")
    st.session_state.api_logs = st.session_state.api_logs[:10]

def fetch_stock_data(sid: str):
    """
    1. 嘗試 AKShare (新浪接口 - 即時)
    2. 失敗則嘗試 yfinance (備援)
    """
    # --- Step 1: AKShare ---
    try:
        # 這裡使用 AKShare 的台股即時接口
        # 注意：AKShare 的台股代碼通常只需要數字
        df = ak.stock_hk_gj_tw_sina(symbol=sid)
        if not df.empty:
            # 取得最新一筆成交價與漲跌幅
            # 新浪接口回傳的欄位包含: name, last, high, low, change, pct_change 等
            last_price = float(df.iloc[0]['last'])
            pct_change = float(df.iloc[0]['pct_change'])
            add_log(f"✅ AKShare 成功: {sid}")
            return {
                "price": last_price,
                "pct": round(pct_change, 2),
                "source": "AKShare (即時)"
            }
    except Exception as e:
        add_log(f"⚠️ AKShare 失敗: {sid}")

    # --- Step 2: yfinance ---
    add_log(f"🔄 轉向 yfinance: {sid}")
    try:
        # 自動判斷上市或上櫃
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
    except:
        pass
    return None

# ===========================================================================
# --- 2. 使用者與 UI 管理 ---
# ===========================================================================
st.set_page_config(page_title="台股監控-AKShare版", layout="centered")

if "initialized" not in st.session_state:
    st.session_state.update({"initialized": True, "my_stocks": [], "api_logs": []})

# 瀏覽器 ID 處理 (略，維持原本邏輯)
def get_browser_id_component():
    components.html(f"""<script>
    (function() {{
        const KEY = "{LS_KEY}";
        let bid = localStorage.getItem(KEY);
        if (!bid) {{ bid = Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem(KEY, bid); }}
        const url = new URL(window.parent.location.href);
        if (url.searchParams.get("bid") !== bid) {{ url.searchParams.set("bid", bid); window.parent.history.replaceState(null, "", url.toString()); window.parent.location.reload(); }}
    }})();
    </script>""", height=0)

get_browser_id_component()
bid = st.query_params.get("bid", "")
if not bid: st.stop()

# 加載用戶股票
def load_stocks(bid):
    path = os.path.join(USER_DATA_DIR, f"{bid}.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    return [{"id": "2330", "name": "台積電"}]

if not st.session_state.my_stocks:
    st.session_state.my_stocks = load_stocks(bid)

# --- UI ---
st.title("🚀 台股即時監控 (AKShare)")

with st.expander("➕ 新增股票"):
    c1, c2, c3 = st.columns([2, 2, 1])
    nid = c1.text_input("代號")
    nname = c2.text_input("名稱")
    if c3.button("新增"):
        st.session_state.my_stocks.append({"id": nid, "name": nname})
        # 儲存邏輯 (略)
        st.rerun()

# 顯示卡片
for idx, stock in enumerate(st.session_state.my_stocks):
    sid, sname = stock["id"], stock["name"]
    q = fetch_stock_data(sid)
    
    with st.container(border=True):
        if q:
            p, pct, src = q["price"], q["pct"], q["source"]
            color = "#ff4b4b" if pct > 0 else "#00ba8b" if pct < 0 else "#31333F"
            c_l, c_r = st.columns([5, 3])
            with c_l:
                st.markdown(f"#### {sname} `{sid}`")
                st.caption(f"來源: {src}")
            with c_r:
                st.markdown(f"<h2 style='color:{color}; text-align:right; margin:0;'>{p}</h2>", unsafe_allow_html=True)
                st.markdown(f"<p style='color:{color}; text-align:right; margin:0;'>{pct}%</p>", unsafe_allow_html=True)
        else:
            st.warning(f"無法載入 {sid}")

# 診斷日誌
with st.sidebar:
    st.header("📡 數據診斷")
    for log in st.session_state.api_logs:
        st.caption(log)

# 自動重新整理
components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)
