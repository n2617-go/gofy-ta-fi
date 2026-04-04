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
MARKET_OPEN   = dt_time(9, 0)
MARKET_CLOSE  = dt_time(13, 30)
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


def today_str() -> str:
    return now_tw().strftime("%Y-%m-%d")


# ===========================================================================
# --- 1. 使用者識別（localStorage → URL query param）---
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
# --- 2. 使用者股票清單（伺服器端 JSON，依 browser_id 區分）---
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
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return list(DEFAULT_STOCKS)


def save_user_stocks(bid: str, stocks: list):
    try:
        with open(user_file(bid), "w", encoding="utf-8") as f:
            json.dump(stocks, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ===========================================================================
# --- 3. 通知狀態管理（觸發門檻 + 重置門檻，每日自動清空）---
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
            if data.get("date") == today:
                return data
        except Exception:
            pass
    return {"date": today, "states": {}}


def save_alert_state(bid: str, state: dict):
    try:
        with open(alert_state_file(bid), "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ===========================================================================
# --- 4. Telegram + FinMind 設定（伺服器端共用）---
# ===========================================================================

def load_tg_config() -> dict:
    if os.path.exists(TG_SAVE_FILE):
        try:
            with open(TG_SAVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
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
        "hist_cache":    {},   # yfinance 歷史快取
        "quote_cache":   {},   # TaiwanStockQuote 即時快取 {stock_id: {pct, price, ...}}
        "my_stocks":     list(DEFAULT_STOCKS),
    })

# browser_id 識別
browser_id = st.query_params.get("bid", "")

if browser_id and st.session_state.get("stocks_loaded_bid") != browser_id:
    st.session_state.my_stocks        = load_user_stocks(browser_id)
    st.session_state.stocks_loaded_bid = browser_id


# ===========================================================================
# --- 6. TaiwanStockQuote：低成本即時報價（每分鐘掃描用）---
# ===========================================================================

def get_finmind_loader():
    """建立並回傳已登入（若有 token）的 FinMind DataLoader。"""
    dl    = DataLoader()
    token = st.session_state.get("finmind_token", "")
    if token:
        dl.login_by_token(api_token=token)
    return dl


@st.cache_data(ttl=60)
def fetch_all_quotes() -> dict:
    """
    用 FinMind TaiwanStockQuote 一次抓取全市場即時報價快照。
    只在開盤中呼叫，ttl=60 確保每分鐘最多呼叫一次（成本極低）。
    回傳 dict：{ stock_id: {"price": float, "pct": float, "open": float} }
    """
    try:
        dl = get_finmind_loader()
        df = dl.taiwan_stock_quote(stock_id="")   # 空字串 = 全市場
        if df is None or df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            sid = str(row.get("stock_id", ""))
            if not sid:
                continue
            try:
                price    = float(row.get("close",            row.get("price", 0)))
                open_p   = float(row.get("open",             0))
                chg_pct  = float(row.get("change_rate",      row.get("ChangeRate", 0)))
                result[sid] = {"price": price, "pct": chg_pct, "open": open_p}
            except Exception:
                continue
        return result
    except Exception as e:
        st.warning(f"TaiwanStockQuote 抓取失敗：{e}")
        return {}


def get_quote(stock_id: str) -> dict:
    """
    從全市場快照中取得單一股票的即時報價。
    回傳 {"price": float, "pct": float, "open": float} 或空 dict。
    """
    quotes = fetch_all_quotes()
    return quotes.get(stock_id, {})


# ===========================================================================
# --- 7. FinMind 盤中動能分析（只在觸發門檻瞬間呼叫）---
# ===========================================================================

def fetch_momentum_analysis(stock_id: str) -> dict:
    """
    抓取該股票最近 10 根 1 分 K，計算：
    - 當前成交量（最新一根）
    - 前 5 分鐘均量
    - 量能比（當前量 / 均量）
    - 動能判斷標籤
    回傳 dict，失敗回傳空 dict。
    """
    try:
        dl    = get_finmind_loader()
        today = today_str()

        # 抓取今日 1 分 K（TaiwanStockKBar）
        df = dl.taiwan_stock_minute(
            stock_id   = stock_id,
            start_date = today,
            end_date   = today,
        )
        if df is None or df.empty:
            return {}

        # 確保有成交量欄位
        vol_col = None
        for col in ["volume", "Volume", "vol"]:
            if col in df.columns:
                vol_col = col
                break
        if vol_col is None:
            return {}

        df = df.sort_values("date") if "date" in df.columns else df
        df[vol_col] = pd.to_numeric(df[vol_col], errors="coerce").fillna(0)

        # 取最後 6 根（1根當前 + 5根計算均量）
        recent = df.tail(6)
        if len(recent) < 2:
            return {}

        cur_vol  = float(recent.iloc[-1][vol_col])          # 當前這分鐘成交量
        avg_vol  = float(recent.iloc[:-1][vol_col].mean())  # 前 5 分鐘均量
        ratio    = cur_vol / avg_vol if avg_vol > 0 else 0

        if ratio >= 2.0:
            momentum_label = "🔥 爆量（當前量 {:.0f}%，均量 {:.0f}x）".format(ratio * 100, ratio)
        elif ratio >= 1.5:
            momentum_label = "📈 放量（當前量為均量 {:.1f} 倍）".format(ratio)
        elif ratio >= 0.8:
            momentum_label = "➡️ 量能正常（{:.1f} 倍均量）".format(ratio)
        else:
            momentum_label = "📉 縮量（當前量僅均量 {:.0f}%）".format(ratio * 100)

        return {
            "cur_vol":        int(cur_vol),
            "avg_vol":        int(avg_vol),
            "ratio":          round(ratio, 2),
            "momentum_label": momentum_label,
        }
    except Exception as e:
        return {"error": str(e)}


# ===========================================================================
# --- 8. 歷史資料快取（yfinance，跨日才重抓）---
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
        except Exception:
            continue
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.astype(float).ffill()
    df.index = pd.to_datetime(df.index).normalize()
    yesterday = pd.Timestamp(today) - timedelta(days=1)
    df = df[df.index <= yesterday]
    cache[stock_id] = {"df": df, "cached_date": today}
    return df.copy()


# ===========================================================================
# --- 9. TaiwanStockQuote 縫合今日棒（取代舊版 FinMind 縫合）---
# ===========================================================================

def stitch_with_quote(hist_df: pd.DataFrame, stock_id: str) -> tuple:
    """
    開盤中：用 TaiwanStockQuote 的即時報價縫合今日棒。
    非開盤：直接回傳歷史資料。
    回傳 (df, source_label)
    """
    if not is_market_open():
        return hist_df, "🗂 yfinance 歷史"

    quote = get_quote(stock_id)
    if not quote:
        return hist_df, "🗂 yfinance 歷史（報價取得失敗）"

    today = pd.Timestamp(today_str())
    # 用昨日收盤價計算今日 Open（若報價沒有 open 則用昨收）
    prev_close = float(hist_df.iloc[-1]["Close"]) if not hist_df.empty else 0
    open_price = quote.get("open", prev_close) or prev_close
    cur_price  = quote["price"]

    today_row = pd.Series({
        "Open":   open_price,
        "High":   max(open_price, cur_price),
        "Low":    min(open_price, cur_price),
        "Close":  cur_price,
        "Volume": 0.0,
    }, name=today)

    today_df = pd.DataFrame([today_row])
    today_df.index.name = hist_df.index.name
    merged = pd.concat([hist_df, today_df])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    return merged, "📡 TaiwanStockQuote 即時縫合"


# ===========================================================================
# --- 10. KD 金叉判斷 ---
# ===========================================================================

def classify_kd_cross(k_now, d_now, k_prev, d_prev):
    if not ((k_prev <= d_prev) and (k_now > d_now)):
        return False, ""
    if (k_now - d_now) < 1.0:
        return False, ""
    avg = (k_now + d_now) / 2
    if avg < 20:
        return True, "✅ KD 低檔金叉（超賣區，可靠度高）"
    elif avg < 80:
        return True, "✅ KD 標準金叉（中段，偏多）"
    return False, ""


# ===========================================================================
# --- 11. 技術指標計算 ---
# ===========================================================================

def calc_indicators(df: pd.DataFrame):
    if len(df) < 30:
        return None
    close = pd.Series(df["Close"].values.flatten(), index=df.index).astype(float)
    high  = pd.Series(df["High"].values.flatten(),  index=df.index).astype(float)
    low   = pd.Series(df["Low"].values.flatten(),   index=df.index).astype(float)
    try:
        try:
            df = df.copy()
            df["MA5"]       = SMAIndicator(close, window=5).sma_indicator()
            df["MA10"]      = SMAIndicator(close, window=10).sma_indicator()
            df["MA20"]      = SMAIndicator(close, window=20).sma_indicator()
            stoch           = StochasticOscillator(high, low, close, window=9)
            df["K"]         = stoch.stoch()
            df["D"]         = stoch.stoch_signal()
            df["MACD_diff"] = MACD(close, window_slow=26, window_fast=12, window_sign=9).macd_diff()
            df["RSI"]       = RSIIndicator(close, window=14).rsi()
            df["BBM"]       = BollingerBands(close, window=20).bollinger_mavg()
        except Exception:
            df = df.copy()
            df["MA5"]       = SMAIndicator(close, n=5).sma_indicator()
            df["MA10"]      = SMAIndicator(close, n=10).sma_indicator()
            df["MA20"]      = SMAIndicator(close, n=20).sma_indicator()
            stoch           = StochasticOscillator(high, low, close, n=9)
            df["K"]         = stoch.stoch()
            df["D"]         = stoch.stoch_signal()
            df["MACD_diff"] = MACD(close, n_slow=26, n_fast=12, n_sign=9).macd_diff()
            df["RSI"]       = RSIIndicator(close, n=14).rsi()
            df["BBM"]       = BollingerBands(close, n=20).bollinger_mavg()
        return df
    except Exception:
        return None


# ===========================================================================
# --- 12. 主分析函數（歷史快取 + TaiwanStockQuote 縫合）---
# ===========================================================================

@st.cache_data(ttl=60)
def fetch_and_analyze(stock_id: str):
    hist_df       = get_history_cached(stock_id)
    if hist_df.empty:
        return None
    df, source    = stitch_with_quote(hist_df, stock_id)
    df            = calc_indicators(df)
    if df is None:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]
    score, details = 0, []

    if last["MA5"] > last["MA10"] > last["MA20"]:
        details.append("✅ 均線多頭排列"); score += 1
    kd_ok, kd_lbl = classify_kd_cross(
        float(last["K"]), float(last["D"]),
        float(prev["K"]), float(prev["D"]))
    if kd_ok:
        details.append(kd_lbl); score += 1
    if last["MACD_diff"] > 0:
        details.append("✅ MACD 柱狀體轉正"); score += 1
    if last["RSI"] > 50:
        details.append("✅ RSI 強勢區"); score += 1
    if last["Close"] > last["BBM"]:
        details.append("✅ 站穩月線(MA20)"); score += 1

    dm = {
        5: ("S (極強)", "🔥 續抱/加碼",   "red"),
        4: ("A (強勢)", "🚀 偏多持股",   "orange"),
        3: ("B (轉強)", "📈 少量試單",   "green"),
        2: ("C (盤整)", "⚖️ 暫時觀望",  "blue"),
        1: ("D (弱勢)", "📉 減碼避險",   "gray"),
        0: ("E (極弱)", "🚫 觀望不進場", "black"),
    }
    grade, action, color = dm[score]

    # 即時漲跌幅：開盤中優先用 TaiwanStockQuote 的即時報價
    if is_market_open():
        quote = get_quote(stock_id)
        pct   = quote.get("pct", 0.0) if quote else 0.0
        price = quote.get("price", float(last["Close"])) if quote else float(last["Close"])
    else:
        pct   = (float(last["Close"]) - float(prev["Close"])) / float(prev["Close"]) * 100
        price = float(last["Close"])

    return {
        "price":   price,
        "pct":     pct,
        "grade":   grade, "action": action, "color": color,
        "details": details, "score": score,
        "k":       float(last["K"]), "d": float(last["D"]),
        "source":  source,
    }


# ===========================================================================
# --- 13. 通知邏輯（觸發時順帶抓 FinMind 動能）---
# ===========================================================================

def send_telegram(tg_token: str, tg_chat_id: str, msg: str):
    try:
        requests.post(
            "https://api.telegram.org/bot" + tg_token + "/sendMessage",
            json={"chat_id": tg_chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass


def check_and_notify(bid: str, stock: dict, pct: float, res: dict,
                     tg_token: str, tg_chat_id: str,
                     tg_threshold: float, tg_reset: float) -> str:
    """
    雙門檻通知邏輯。
    觸發時同步呼叫 FinMind 盤中動能分析，一併附在通知內。
    回傳通知狀態標籤（供 UI 顯示）。
    """
    if not tg_token or not tg_chat_id:
        return "⚪ 未設定通知"

    alert_state = load_alert_state(bid)
    stock_id    = stock["id"]
    states      = alert_state.setdefault("states", {})
    s           = states.setdefault(stock_id, {
        "alerted": False, "last_pct": 0.0, "alerted_at": "",
        "momentum": {},
    })

    abs_pct = abs(pct)
    label   = ""

    if s["alerted"]:
        # 已鎖定：檢查是否達到重置門檻
        if abs_pct <= tg_reset:
            s["alerted"]    = False
            s["alerted_at"] = ""
            s["momentum"]   = {}
            label = "🔓 已重置（漲跌 {:.2f}%，回落至重置門檻 {:.1f}% 以下）".format(pct, tg_reset)
        else:
            at  = s["alerted_at"]
            mom = s.get("momentum", {})
            mom_txt = mom.get("momentum_label", "") if mom else ""
            label = "🔒 鎖定中（{} 已發送，需回落至 {:.1f}% 以下）".format(at, tg_reset)
            if mom_txt:
                label += "　" + mom_txt
    else:
        # 未鎖定：檢查是否達到觸發門檻
        if abs_pct >= tg_threshold:
            direction = "📈 上漲" if pct > 0 else "📉 下跌"

            # ── 觸發瞬間才呼叫 FinMind 抓盤中動能 ──
            momentum = fetch_momentum_analysis(stock_id)
            s["momentum"] = momentum

            mom_line = ""
            if momentum and "momentum_label" in momentum:
                cur_v = momentum.get("cur_vol", 0)
                avg_v = momentum.get("avg_vol", 0)
                ratio = momentum.get("ratio", 0)
                mom_line = (
                    "\n\n<b>📊 盤中動能分析</b>\n"
                    "當前量：{:,} 張　前5分均量：{:,} 張\n"
                    "量能比：{:.1f} 倍　{}".format(
                        cur_v, avg_v, ratio, momentum["momentum_label"])
                )
            elif momentum.get("error"):
                mom_line = "\n\n📊 動能分析：取得失敗（{}）".format(momentum["error"])

            name   = stock["name"]
            price  = res["price"]
            grade  = res["grade"]
            action = res["action"]
            inds   = ", ".join(res["details"]) if res["details"] else "無"
            thresh = tg_threshold
            reset  = tg_reset

            msg = (
                "🔔 <b>【價格異動通知】</b>\n\n"
                "標的：<b>{} ({})</b>\n"
                "目前股價：<b>{:.2f}</b>\n"
                "今日漲跌：<b>{:+.2f}%</b> {}\n"
                "技術評級：{}\n"
                "建議決策：<b>{}</b>\n\n"
                "符合指標：{}\n\n"
                "⚠️ 觸發門檻：{}%　重置門檻：{}%"
                "{}"
            ).format(name, stock_id, price, pct, direction,
                     grade, action, inds, thresh, reset, mom_line)

            send_telegram(tg_token, tg_chat_id, msg)
            s["alerted"]    = True
            s["alerted_at"] = now_tw().strftime("%H:%M")
            label = "✅ 已發送通知（{}，漲跌 {:+.2f}%）".format(s["alerted_at"], pct)
            if momentum and "momentum_label" in momentum:
                label += "　" + momentum["momentum_label"]
        else:
            label = "⚪ 監控中（{:+.2f}%，門檻 ±{:.1f}%）".format(pct, tg_threshold)

    s["last_pct"] = pct
    save_alert_state(bid, alert_state)
    return label


# ===========================================================================
# --- 14. 介面 ---
# ===========================================================================
st.set_page_config(page_title="台股決策系統 V7.5", layout="centered")
st.title("🤖 台股 AI 技術分級決策支援")

# ── browser_id 初始化 ─────────────────────────────────────────────────────
if not browser_id:
    get_browser_id_component()
    st.info("⏳ 初始化中，請稍候...")
    st.stop()

# ── 開盤中自動每 60 秒刷新 ───────────────────────────────────────────────
if is_market_open():
    components.html("""
    <script>
    setTimeout(function() { window.parent.location.reload(); }, 60000);
    </script>
    """, height=0)
    st.success(
        "🟢 **開盤中** — 每 60 秒自動更新｜"
        "報價來自 TaiwanStockQuote（低成本掃描），"
        "觸發門檻時才呼叫 FinMind 動能分析"
    )
else:
    st.info("🔵 **非開盤時間**（{}）— 使用 yfinance 歷史快取".format(now_tw().strftime("%H:%M")))

st.caption("📌 您的專屬清單已儲存於此瀏覽器，重新整理或關閉後仍會保留。")

# ── 新增自選股票 ──────────────────────────────────────────────────────────
with st.container(border=True):
    st.subheader("🔍 新增自選股票")
    c1, c2, c3 = st.columns([2, 3, 1.2])
    input_id   = c1.text_input("代號", key="add_id")
    input_name = c2.text_input("名稱", key="add_name")
    if c3.button("➕ 新增", use_container_width=True):
        if input_id and input_name:
            if not any(s["id"] == input_id for s in st.session_state.my_stocks):
                st.session_state.my_stocks.append({"id": input_id, "name": input_name})
                save_user_stocks(browser_id, st.session_state.my_stocks)
                st.rerun()

# ── Sidebar ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 設定")

    st.subheader("📡 FinMind")
    st.session_state.finmind_token = st.text_input(
        "API Token（選填）", type="password",
        value=st.session_state.finmind_token,
        help=(
            "用於 TaiwanStockQuote 全市場掃描 及 觸發時的盤中動能分析。\n"
            "未填使用免費版（有速率限制）。"
        ),
    )

    st.divider()

    st.subheader("🔔 Telegram 通知")
    st.session_state.tg_token   = st.text_input(
        "Bot Token", type="password", value=st.session_state.tg_token)
    st.session_state.tg_chat_id = st.text_input(
        "Chat ID", value=st.session_state.tg_chat_id)

    st.markdown("**門檻設定**")
    col_a, col_b = st.columns(2)
    st.session_state.tg_threshold = col_a.number_input(
        "觸發門檻 (%)", min_value=0.1, max_value=20.0,
        value=float(st.session_state.tg_threshold), step=0.5,
        help="漲跌幅達到此值時：發送 Telegram 通知 + 抓 FinMind 動能分析。",
    )
    st.session_state.tg_reset = col_b.number_input(
        "重置門檻 (%)", min_value=0.1, max_value=20.0,
        value=float(st.session_state.tg_reset), step=0.5,
        help="鎖定後，漲跌幅回落至此值以下才解鎖，等待下次觸發。",
    )

    if st.session_state.tg_reset >= st.session_state.tg_threshold:
        st.warning("⚠️ 重置門檻必須小於觸發門檻")
    else:
        buf = st.session_state.tg_threshold - st.session_state.tg_reset
        st.caption(
            "緩衝區 ±{:.1f}%　（觸發 {}% → 需回落至 {}% 才重置）".format(
                buf, st.session_state.tg_threshold, st.session_state.tg_reset)
        )

    if st.button("💾 儲存設定"):
        if st.session_state.tg_reset < st.session_state.tg_threshold:
            save_tg_config()
            st.success("已儲存")
        else:
            st.error("重置門檻必須小於觸發門檻，請修正後再儲存。")

    st.divider()

    # 手動掃描（強制發送，不受鎖定影響）
    if st.button("🚀 手動掃描並發送通知", use_container_width=True):
        st.cache_data.clear()
        found = 0
        for s in st.session_state.my_stocks:
            res = fetch_and_analyze(s["id"])
            if res and abs(res["pct"]) >= st.session_state.tg_threshold:
                name   = s["name"]
                sid    = s["id"]
                price  = res["price"]
                pct_v  = res["pct"]
                grade  = res["grade"]
                action = res["action"]
                inds   = ", ".join(res["details"]) if res["details"] else "無"
                msg = (
                    "🔔 <b>【手動掃描通知】</b>\n\n"
                    "標的：<b>{} ({})</b>\n"
                    "目前股價：<b>{:.2f}</b>\n"
                    "今日漲跌：<b>{:+.2f}%</b>\n"
                    "技術評級：{}\n"
                    "建議決策：<b>{}</b>\n\n"
                    "符合指標：{}"
                ).format(name, sid, price, pct_v, grade, action, inds)
                send_telegram(st.session_state.tg_token, st.session_state.tg_chat_id, msg)
                found += 1
        st.success("掃描完成，已發送 {} 則通知".format(found))

    st.divider()
    with st.expander("📖 資料來源說明"):
        st.markdown("""
**開盤中掃描流程（每 60 秒）**
1. `TaiwanStockQuote` 一次抓全市場即時報價（低成本）
2. 用報價縫合今日棒到歷史 K 線，計算技術指標
3. 掃描使用者清單是否觸及門檻
4. **觸發瞬間才呼叫 FinMind** 抓 1 分 K，分析盤中動能

**非開盤**：只用 yfinance 歷史快取，完全不呼叫 FinMind。
        """)
    with st.expander("📖 門檻說明"):
        st.markdown("""
**觸發門檻**：漲跌幅達到設定值，且未鎖定 → 發送 Telegram + 附上動能分析，進入鎖定。

**重置門檻**：鎖定後漲跌幅回落至此值以下 → 解鎖，等待下次觸發。

**緩衝區**：兩者之間的區間，避免在門檻附近震盪時重複通知。
        """)
    with st.expander("📖 KD 金叉說明"):
        st.markdown("""
1. 真實交叉：前 K ≤ D，本根 K > D
2. 幅度 ≥ 1（排除噪音假叉）
3. KD < 20 → 低檔金叉 ✅　20~79 → 標準金叉 ✅　≥ 80 → 高檔鈍化 ❌
        """)

# ── 股票清單 ──────────────────────────────────────────────────────────────
st.divider()

tg_ok = (
    bool(st.session_state.tg_token) and
    bool(st.session_state.tg_chat_id) and
    st.session_state.tg_reset < st.session_state.tg_threshold
)

for idx, stock in enumerate(st.session_state.my_stocks):
    res = fetch_and_analyze(stock["id"])
    if res:
        # 開盤中自動執行通知邏輯（包含觸發時的 FinMind 動能分析）
        if is_market_open() and tg_ok:
            alert_label = check_and_notify(
                bid          = browser_id,
                stock        = stock,
                pct          = res["pct"],
                res          = res,
                tg_token     = st.session_state.tg_token,
                tg_chat_id   = st.session_state.tg_chat_id,
                tg_threshold = st.session_state.tg_threshold,
                tg_reset     = st.session_state.tg_reset,
            )
        elif not is_market_open():
            alert_label = "🔵 非開盤時間，通知暫停"
        else:
            alert_label = "⚪ 請先設定 Telegram Token 與 Chat ID"

        with st.container(border=True):
            col_info, col_metric, col_del = st.columns([3, 2, 0.6])
            with col_info:
                name = stock["name"]
                sid  = stock["id"]
                st.write("### {} ({})".format(name, sid))
                st.caption("資料來源：{}".format(res["source"]))
                st.markdown("評級：`{}`".format(res["grade"]))
                color  = res["color"]
                action = res["action"]
                st.markdown(
                    "**建議決策：<span style='color:{}'>{}</span>**".format(color, action),
                    unsafe_allow_html=True,
                )
                indicators = "　".join(res["details"]) if res["details"] else "無"
                st.markdown("符合指標：{}".format(indicators))
                st.caption("KD 值：K={:.1f} / D={:.1f}".format(res["k"], res["d"]))
                st.caption("通知狀態：{}".format(alert_label))
            with col_metric:
                st.metric("股價", "{:.2f}".format(res["price"]),
                          "{:+.2f}%".format(res["pct"]), delta_color="inverse")
            with col_del:
                if st.button("🗑️", key="del_" + stock["id"]):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
    else:
        with st.container(border=True):
            name = stock["name"]
            sid  = stock["id"]
            st.warning("⚠️ **{} ({})** 資料抓取失敗，請確認代號或稍後再試。".format(name, sid))

if st.button("🔄 手動重新整理"):
    st.cache_data.clear()
    st.rerun()
