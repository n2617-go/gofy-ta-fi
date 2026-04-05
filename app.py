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
# --- 14. 主分析函數（歷史快取 + TaiwanStockQuote 縫合）---
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
        "hist_df": hist_df,   # 供盤後 5MAV 計算使用
    }


# ===========================================================================
# --- 15. 通知邏輯（觸發時順帶抓 FinMind 動能）---
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
            at         = s["alerted_at"]
            mom        = s.get("momentum", {})
            mom_txt    = mom.get("momentum_label", "") if mom else ""
            short_impl = mom.get("short_impl", "") if mom else ""
            label = "🔒 鎖定中（{} 已發送，需回落至 {:.1f}% 以下）".format(at, tg_reset)
            if mom_txt:
                label += "　" + mom_txt
            if short_impl:
                label += "　" + short_impl
    else:
        # 未鎖定：檢查是否達到觸發門檻
        if abs_pct >= tg_threshold:
            direction = "📈 上漲" if pct > 0 else "📉 下跌"

            # ── 觸發瞬間才呼叫 FinMind 抓盤中動能 ──
            momentum = fetch_momentum_analysis(stock_id, pct=pct, tg_threshold=tg_threshold)
            s["momentum"] = momentum

            mom_line = ""
            if momentum and "momentum_label" in momentum:
                cur_v      = momentum.get("cur_vol", 0)
                avg_v      = momentum.get("avg_vol", 0)
                ratio      = momentum.get("ratio", 0)
                short_impl = momentum.get("short_impl", "")
                impl_line  = "\n" + short_impl if short_impl else ""
                mom_line = (
                    "\n\n<b>📊 盤中動能分析</b>\n"
                    "當前量：{:,} 張　前5分均量：{:,} 張\n"
                    "量能比：{:.1f} 倍　{}{}"
                ).format(cur_v, avg_v, ratio, momentum["momentum_label"], impl_line)
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
            s["alerted"]        = True
            s["alerted_at"]     = now_tw().strftime("%H:%M")
            s["ever_triggered"] = True   # 今日曾觸發，盤後分析用，重置後仍保留
            label = "✅ 已發送通知（{}，漲跌 {:+.2f}%）".format(s["alerted_at"], pct)
            if momentum and "momentum_label" in momentum:
                label += "　" + momentum["momentum_label"]
            short_impl_sent = momentum.get("short_impl", "") if momentum else ""
            if short_impl_sent:
                label += "　" + short_impl_sent
        else:
            label = "⚪ 監控中（{:+.2f}%，門檻 ±{:.1f}%）".format(pct, tg_threshold)

    s["last_pct"] = pct
    save_alert_state(bid, alert_state)
    return label


# ===========================================================================
# --- 16. 介面 ---
# ===========================================================================
st.set_page_config(page_title="台股決策系統 V7.5", layout="centered")

# ── 全域 CSS：縮減間距、美化卡片 ──
st.markdown("""
<style>
/* 全域縮減 Streamlit 預設間距 */
.block-container { padding-top: 1rem !important; padding-bottom: 1rem !important; }
[data-testid="stVerticalBlock"] > div { gap: 0.3rem !important; }

/* 股票卡片 */
.stock-card {
    background: var(--background-color, #1e1e2e);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 12px 14px;
    margin-bottom: 8px;
}

/* 股票標題列 */
.card-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 6px;
}
.card-title {
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: 0.01em;
}
.card-price-block {
    text-align: right;
    line-height: 1.2;
}
.card-price {
    font-size: 1.7rem;
    font-weight: 800;
    letter-spacing: -0.02em;
}
.card-pct-up   { color: #ff6b6b; font-size: 1.0rem; font-weight: 600; }
.card-pct-down { color: #51cf66; font-size: 1.0rem; font-weight: 600; }
.card-pct-flat { color: #868e96; font-size: 1.0rem; font-weight: 600; }

/* 資訊列 */
.card-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
    font-size: 1.0rem;
    color: rgba(255,255,255,0.75);
}
.card-label { color: rgba(255,255,255,0.45); font-size: 0.9rem; margin-right: 2px; }

/* Badge 標籤 */
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.9rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    white-space: nowrap;
}
.badge-grade  { background: rgba(99,179,237,0.18);  color: #63b3ed; }
.badge-ind    { background: rgba(72,187,120,0.15);  color: #48bb78; font-size: 0.88rem; }
.badge-ah     { background: rgba(246,173,85,0.18);  color: #f6ad55; }
.badge-alert  { background: rgba(160,174,192,0.12); color: #a0aec0; }
.badge-src    { background: rgba(255,255,255,0.06); color: rgba(255,255,255,0.35);
                font-size: 0.82rem; }

/* 決策文字顏色 */
.action-red    { color: #fc8181; font-weight: 700; }
.action-orange { color: #f6ad55; font-weight: 700; }
.action-green  { color: #68d391; font-weight: 700; }
.action-blue   { color: #63b3ed; font-weight: 700; }
.action-gray   { color: #a0aec0; font-weight: 700; }
.action-black  { color: #718096; font-weight: 700; }

/* 刪除按鈕縮小 */
button[kind="secondary"] { padding: 2px 8px !important; font-size: 0.75rem !important; }

/* 隱藏 metric 預設大字 */
[data-testid="stMetric"] { display: none !important; }

/* 強制按鈕列橫向排列，不換行 */
[data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap !important;
    gap: 4px !important;
}
[data-testid="stHorizontalBlock"] > div {
    min-width: 0 !important;
    flex: 0 0 auto !important;
}
</style>
""", unsafe_allow_html=True)

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
                # 盤後時段附上盤後意涵（ah_impl 本身已含標籤，直接換行附上）
                ah_line = ""
                if is_after_hours():
                    _ah_s_m  = load_alert_state(browser_id).get("states", {}).get(sid, {})
                    _ah_impl = _ah_s_m.get("ah_impl", "")
                    if _ah_impl:
                        ah_line = "\n{}".format(_ah_impl)

                msg = (
                    "🔔 <b>【手動掃描通知】</b>\n\n"
                    "標的：<b>{} ({})</b>\n"
                    "目前股價：<b>{:.2f}</b>\n"
                    "今日漲跌：<b>{:+.2f}%</b>\n"
                    "技術評級：{}\n"
                    "建議決策：<b>{}</b>\n\n"
                    "符合指標：{}{}"
                ).format(name, sid, price, pct_v, grade, action, inds, ah_line)
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
    with st.expander("📖 盤後意涵說明"):
        st.markdown("""
**顯示時機**：收盤後 14:00 起（FinMind 資料此時較完整）

**5 日均量基準（5MAV）**：取今天以前連續 5 個交易日的成交量平均。

**四種判斷**：

| 漲跌 | 量能 | 意涵 |
|---|---|---|
| 上漲達門檻 | 收盤量 > 5MAV × 1.1 | 📈 量增上漲，可考慮留倉 |
| 上漲達門檻 | 收盤量 < 5MAV × 0.9 | ⚠️ 量縮上漲，不宜追高 |
| 下跌達門檻 | 收盤量 > 5MAV × 1.1 | 💣 趨勢轉弱，建議避開 |
| 下跌達門檻 | 收盤量 < 5MAV × 0.9 | 🔍 量縮下跌，可尋買點 |

**Telegram**：每日每檔只發一次，不重複通知。
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

        # ── 整理卡片所需資料 ──
        name   = stock["name"]
        sid    = stock["id"]
        price  = res["price"]
        pct    = res["pct"]
        grade  = res["grade"]
        action = res["action"]
        color  = res["color"]
        source = res["source"]
        kval   = res["k"]
        dval   = res["d"]

        # 漲跌幅樣式
        if pct > 0:
            pct_cls  = "card-pct-up"
            pct_sign = "▲"
        elif pct < 0:
            pct_cls  = "card-pct-down"
            pct_sign = "▼"
        else:
            pct_cls  = "card-pct-flat"
            pct_sign = "─"

        # 決策文字顏色 class
        action_cls = "action-" + color

        # 符合指標 badges
        ind_badges = "".join(
            '<span class="badge badge-ind">{}</span>'.format(d)
            for d in res["details"]
        ) if res["details"] else '<span style="color:rgba(255,255,255,0.3);font-size:0.72rem;">無</span>'

        # 短線意涵
        _astate  = load_alert_state(browser_id)
        _astates = _astate.get("states", {})
        _smom    = _astates.get(sid, {}).get("momentum", {})
        _simpl   = _smom.get("short_impl", "") if _smom else ""
        if _simpl and is_market_open():
            full_label = "{}　{}".format(alert_label, _simpl)
        else:
            full_label = alert_label

        # 盤後意涵
        ah_impl = ""
        if is_after_hours():
            hist_df_for_ah = res.get("hist_df", pd.DataFrame())
            ah_impl = run_afterhours_analysis(
                bid          = browser_id,
                stock        = stock,
                pct          = pct,
                hist_df      = hist_df_for_ah,
                tg_threshold = st.session_state.tg_threshold,
            )
            if not ah_impl:
                _ah_s  = _astates.get(sid, {})
                _vol   = _ah_s.get("ah_vol", 0)
                _mav5  = _ah_s.get("ah_mav5", 0)
                _ratio = _ah_s.get("ah_ratio", 0)
                if _vol > 0 and _mav5 > 0:
                    ah_impl = "📊 盤後量能：{:,}張 / 均量{:,}張 / {:.2f}倍".format(
                        _vol, _mav5, _ratio)
                else:
                    ah_impl = "📊 FinMind 資料更新中，請稍後重新整理"

        ah_badge = (
            '<div class="card-row"><span class="badge badge-ah">{}</span></div>'.format(ah_impl)
            if ah_impl else ""
        )

        # ── HTML 卡片 ──
        card_html = """
        <div class="stock-card">
          <div class="card-header">
            <span class="card-title">{name} <span style="font-weight:400;font-size:0.85rem;opacity:0.6;">({sid})</span></span>
            <div class="card-price-block">
              <div class="card-price">{price:.2f}</div>
              <div class="{pct_cls}">{pct_sign} {pct_abs:.2f}%</div>
            </div>
          </div>
          <div class="card-row">
            <span class="badge badge-grade">{grade}</span>
            <span class="{action_cls}">{action}</span>
            <span class="badge badge-src">{source}</span>
          </div>
          <div class="card-row">
            <span class="card-label">指標</span>{ind_badges}
          </div>
          <div class="card-row">
            <span class="card-label">KD</span>
            <span>K={kval:.1f} D={dval:.1f}</span>
            <span style="margin-left:8px;opacity:0.5;">│</span>
            <span class="badge badge-alert">{alert}</span>
          </div>
          {ah_badge}
        </div>
        """.format(
            name=name, sid=sid, price=price,
            pct_cls=pct_cls, pct_sign=pct_sign, pct_abs=abs(pct),
            grade=grade, action_cls=action_cls, action=action, source=source,
            ind_badges=ind_badges, kval=kval, dval=dval,
            alert=full_label, ah_badge=ah_badge,
        )

        # 卡片 + 下方橫向按鈕列（🗑 ⬆️ ⬇️）
        total = len(st.session_state.my_stocks)
        with st.container():
            st.markdown(card_html, unsafe_allow_html=True)
            # 強制橫向：用 use_container_width=False + gap 設為 small
            b1, b2, b3, _ = st.columns([1, 1, 1, 9], gap="small")
            with b1:
                if st.button("🗑", key="del_" + sid, use_container_width=True):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
            with b2:
                if idx > 0:
                    if st.button("⬆️", key="up_" + sid, use_container_width=True):
                        stocks = st.session_state.my_stocks
                        stocks[idx], stocks[idx - 1] = stocks[idx - 1], stocks[idx]
                        save_user_stocks(browser_id, stocks)
                        st.rerun()
            with b3:
                if idx < total - 1:
                    if st.button("⬇️", key="dn_" + sid, use_container_width=True):
                        stocks = st.session_state.my_stocks
                        stocks[idx], stocks[idx + 1] = stocks[idx + 1], stocks[idx]
                        save_user_stocks(browser_id, stocks)
                        st.rerun()
    else:
        with st.container():
            name  = stock["name"]
            sid   = stock["id"]
            total = len(st.session_state.my_stocks)
            st.warning("⚠️ **{} ({})** 資料抓取失敗，請確認代號或稍後再試。".format(name, sid))
            b1, b2, b3, _ = st.columns([1, 1, 1, 9], gap="small")
            with b1:
                if st.button("🗑", key="del_err_" + sid, use_container_width=True):
                    st.session_state.my_stocks.pop(idx)
                    save_user_stocks(browser_id, st.session_state.my_stocks)
                    st.rerun()
            with b2:
                if idx > 0:
                    if st.button("⬆️", key="up_err_" + sid, use_container_width=True):
                        stocks = st.session_state.my_stocks
                        stocks[idx], stocks[idx - 1] = stocks[idx - 1], stocks[idx]
                        save_user_stocks(browser_id, stocks)
                        st.rerun()
            with b3:
                if idx < total - 1:
                    if st.button("⬇️", key="dn_err_" + sid, use_container_width=True):
                        stocks = st.session_state.my_stocks
                        stocks[idx], stocks[idx + 1] = stocks[idx + 1], stocks[idx]
                        save_user_stocks(browser_id, stocks)
                        st.rerun()

if st.button("🔄 手動重新整理"):
    st.cache_data.clear()
    st.rerun()
