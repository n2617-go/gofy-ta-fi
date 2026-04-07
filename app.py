def fetch_stock_data_with_priority(sid: str, token: str):
    """
    優先級 1: FinMind 單一股票快照 (避免全市場快照的權限問題)
    優先級 2: yfinance 備援
    """
    clean_token = token.strip() if token else ""
    
    # --- 嘗試 FinMind 單一快照 ---
    if clean_token:
        # 單一快照的 URL 加上 stock_id 參數
        fm_url = f"https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"
        params = {"token": clean_token, "stock_id": sid}
        try:
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
            pass # 失敗則進入下一階段

    # --- 嘗試 yfinance 備援 (如果 FinMind 失敗或沒 Token) ---
    try:
        # 自動切換上市(.TW)或上櫃(.TWO)
        for suffix in [".TW", ".TWO"]:
            t = yf.Ticker(f"{sid}{suffix}")
            fast = t.fast_info
            if fast.last_price is not None and fast.last_price > 0:
                return {
                    "price": round(fast.last_price, 2),
                    "pct": round(((fast.last_price - fast.previous_close) / fast.previous_close) * 100, 2),
                    "source": f"yfinance ({'延遲' if suffix=='.TW' else '即時'})"
                }
    except:
        return None
