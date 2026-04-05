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
AFTERHOURS_START = dt_time(14, 0)   # 盤後意涵開始顯示時間
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
    """
    判斷是否應顯示盤後意涵，涵蓋三個時段：
    1. 當天 14:00 ~ 23:59（收盤後當日）
    2. 隔天 00:00 ~ 08:59（次日開盤前）
    3. 週六、週日全天（上週五收盤後）
    排除：週一 09:00 後（新的一個交易日開始）
    """
    n    = now_tw()
    t    = n.time()
    wday = n.weekday()  # 0=週一 ... 6=週日

    # 週六、週日全天顯示（上週五盤後）
    if wday >= 5:
        return True

    # 平日 14:00 ~ 23:59（當天盤後）
    if t >= AFTERHOURS_START:
        return True

    # 平日 00:00 ~ 08:59（次日開盤前，仍顯示前一日盤後）
    if t < MARKET_OPEN:
        return True

    # 其餘（09:00 ~ 13:59）= 開盤中或收盤前，不顯示
    return False


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

def classify_short_implication(pct: float, ratio: float, tg_threshold: float) -> str:
    """
    四種短線意涵判斷：
    1. 上漲達門檻 + 放量(ratio >= 1.5) → 帶量突破
    2. 上漲達門檻 + 縮量(ratio <  1.0) → 虛假拉抬
    3. 下跌達門檻 + 放量(ratio >= 1.5) → 帶量殺盤
    4. 下跌達門檻 + 縮量(ratio <  1.0) → 洗盤觀察
    其餘情況回傳空字串。
    """
    is_up   = pct >= tg_threshold
    is_down = pct <= -tg_threshold
    is_vol_up   = ratio >= 1.5
    is_vol_down = ratio <  1.0

    if is_up and is_vol_up:
        return "🚀 短線意涵：帶量突破"
    elif is_up and is_vol_down:
        return "⚠️ 短線意涵：虛假拉抬"
    elif is_down and is_vol_up:
        return "💣 短線意涵：帶量殺盤"
    elif is_down and is_vol_down:
        return "🔍 短線意涵：洗盤觀察"
    return ""


def fetch_momentum_analysis(stock_id: str, pct: float = 0.0,
                             tg_threshold: float = 3.0) -> dict:
    """
    抓取該股票最近 6 根 1 分 K，計算：
    - 當前成交量（最新一根）
    - 前 5 分鐘均量
    - 量能比（當前量 / 均量）
    - 動能判斷標籤
    - 短線意涵（四種情境）
    回傳 dict，失敗回傳空 dict。
    """
    try:
        dl    = get_finmind_loader()
        today = today_str()

        df = dl.taiwan_stock_minute(
            stock_id   = stock_id,
            start_date = today,
            end_date   = today,
        )
        if df is None or df.empty:
            return {}

        vol_col = None
        for col in ["volume", "Volume", "vol"]:
            if col in df.columns:
                vol_col = col
                break
        if vol_col is None:
            return {}

        df = df.sort_values("date") if "date" in df.columns else df
        df[vol_col] = pd.to_numeric(df[vol_col], errors="coerce").fillna(0)

        recent = df.tail(6)
        if len(recent) < 2:
            return {}

        cur_vol = float(recent.iloc[-1][vol_col])
        avg_vol = float(recent.iloc[:-1][vol_col].mean())
        ratio   = cur_vol / avg_vol if avg_vol > 0 else 0.0

        if ratio >= 2.0:
            momentum_label = "🔥 爆量（{:.1f} 倍均量）".format(ratio)
        elif ratio >= 1.5:
            momentum_label = "📈 放量（{:.1f} 倍均量）".format(ratio)
        elif ratio >= 1.0:
            momentum_label = "➡️ 量能正常（{:.1f} 倍均量）".format(ratio)
        else:
            momentum_label = "📉 縮量（均量 {:.0f}%）".format(ratio * 100)

        short_impl = classify_short_implication(pct, ratio, tg_threshold)

        return {
            "cur_vol":        int(cur_vol),
            "avg_vol":        int(avg_vol),
            "ratio":          round(ratio, 2),
            "momentum_label": momentum_label,
            "short_impl":     short_impl,
        }
    except Exception as e:
        return {"error": str(e)}


# ===========================================================================
# --- 8. 盤後意涵分析 ---
# ===========================================================================




def get_5mav_from_history(hist_df: pd.DataFrame) -> float:
    """
    從 yfinance 歷史 DataFrame 取「今天以前」連續 5 個交易日的成交量平均。
    yfinance Volume 單位為「股」，除以 1000 轉換成「張」與 FinMind 統一。
    hist_df 已在 get_history_cached() 中過濾掉今天，直接取最後 5 筆即可。
    """
    if hist_df.empty or "Volume" not in hist_df.columns:
        return 0.0
    vols = pd.to_numeric(hist_df["Volume"], errors="coerce").dropna()
    if len(vols) < 5:
        avg = float(vols.mean()) if len(vols) > 0 else 0.0
    else:
        avg = float(vols.iloc[-5:].mean())
    return round(avg / 1000)   # 股 → 張


def fetch_finmind_close_volume(stock_id: str) -> tuple:
    """
    用 FinMind 抓最近一個交易日的收盤成交量。
    FinMind taiwan_stock_daily 的成交量欄位名稱為 Trading_Volume（大寫）。
    抓近 7 天取最後一筆，確保拿到最新交易日。
    回傳 (volume: float, date: str)，失敗回傳 (0.0, "")。
    """
    try:
        dl         = get_finmind_loader()
        today      = today_str()
        start_date = (now_tw() - timedelta(days=7)).strftime("%Y-%m-%d")
        df         = dl.taiwan_stock_daily(
            stock_id   = stock_id,
            start_date = start_date,
            end_date   = today,
        )
        if df is None or df.empty:
            return 0.0, ""

        # 排序取最後一筆（最新交易日）
        date_col = "date" if "date" in df.columns else df.columns[0]
        df        = df.sort_values(date_col)
        row       = df.iloc[-1]
        data_date = str(row.get(date_col, ""))

        # FinMind taiwan_stock_daily 成交量欄位名稱（依優先順序嘗試）
        # Trading_Volume 單位為「股」，除以 1000 四捨五入轉換成「張」
        for col in ["Trading_Volume", "volume", "Volume", "vol", "trading_volume"]:
            if col in row.index and row[col] not in [None, "", "nan"]:
                val = float(row[col])
                if val > 0:
                    val_lots = round(val / 1000)   # 股 → 張
                    return float(val_lots), data_date

        # 除錯：印出實際欄位名稱供檢查
        return 0.0, "欄位: " + str(list(row.index))
    except Exception as e:
        return 0.0, "錯誤: " + str(e)


def classify_afterhours_implication(pct: float, close_vol: float,
                                     mav5: float, tg_threshold: float) -> str:
    """
    四種盤後意涵判斷：
    1. 上漲達門檻 + 量增(> 1.1 倍 5MAV) → 量增上漲，可考慮留倉
    2. 上漲達門檻 + 量縮(< 0.9 倍 5MAV) → 量縮上漲，不宜追高
    3. 下跌達門檻 + 量增(> 1.1 倍 5MAV) → 趨勢轉弱，建議避開
    4. 下跌達門檻 + 量縮(< 0.9 倍 5MAV) → 量縮下跌，可尋買點
    其餘（不足門檻 / 量能中性）回傳空字串。
    """
    if mav5 <= 0 or close_vol <= 0:
        return ""
    ratio   = close_vol / mav5
    is_up   = pct >= tg_threshold
    is_down = pct <= -tg_threshold
    vol_up  = ratio > 1.1
    vol_dwn = ratio < 0.9

    if is_up and vol_up:
        return "📈 盤後意涵：量增上漲，可考慮留倉"
    elif is_up and vol_dwn:
        return "⚠️ 盤後意涵：量縮上漲，不宜追高"
    elif is_down and vol_up:
        return "💣 盤後意涵：趨勢轉弱，建議避開"
    elif is_down and vol_dwn:
        return "🔍 盤後意涵：量縮下跌，可尋買點"
    return ""


def run_afterhours_analysis(bid: str, stock: dict, pct: float,
                             hist_df: pd.DataFrame,
                             tg_threshold: float) -> str:
    """
    盤後意涵主流程（所有自選股，14:00 後皆執行）：
    1. 先查 alert_state 快取，有存好的結果就直接回傳，不重呼叫 FinMind
    2. 快取沒有才呼叫 FinMind 抓收盤量，計算後存入快取
    3. 不受觸發門檻限制，所有股票都分析
    回傳意涵標籤，資料不足時回傳空字串。
    """
    stock_id   = stock["id"]
    alert_state = load_alert_state(bid)
    states      = alert_state.setdefault("states", {})
    s           = states.setdefault(stock_id, {})

    # ── 快取命中判斷：日期相同 且 門檻相同 才直接回傳 ──
    # 門檻改變時需重新計算（避免調整門檻後仍顯示舊結果）
    cached_date    = s.get("ah_date", "")
    cached_thresh  = s.get("ah_threshold", None)
    cache_valid    = (
        "ah_impl" in s and
        cached_date == today_str() and
        cached_thresh == tg_threshold
    )
    if cache_valid:
        return s["ah_impl"]

    # ── 快取未命中或門檻已變更：重新計算 ──
    mav5 = get_5mav_from_history(hist_df)
    if mav5 <= 0:
        return ""

    close_vol, data_date = fetch_finmind_close_volume(stock_id)
    if close_vol <= 0:
        return ""

    impl = classify_afterhours_implication(pct, close_vol, mav5, tg_threshold)

    # ── 存入快取 ──
    s["ah_impl"]      = impl
    s["ah_date"]      = today_str()
    s["ah_threshold"] = tg_threshold   # 記錄計算時的門檻，門檻變動時自動失效
    s["ah_data_date"] = data_date
    s["ah_vol"]       = int(close_vol)
    s["ah_mav5"]      = int(mav5)
    s["ah_ratio"]     = round(close_vol / mav5, 2) if mav5 > 0 else 0
    save_alert_state(bid, alert_state)

    return impl


# ===========================================================================
# --- 10. 歷史資料快取（yfinance，跨日才重抓）---
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
# --- 11. TaiwanStockQuote 縫合今日棒（取代舊版 FinMind 縫合）---
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
# --- 12. KD 金叉判斷 ---
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
# --- 13. 技術指標計算 ---
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
            df["MA10"]      = SMAIndicator(close, n=10).