import yfinance as yf
import asyncio
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=2)


def _fetch_price_sync(stock_code: str) -> dict:
    """
    Ambil data harga saham dari Yahoo Finance (IDX: suffix .JK).
    Dijalankan di thread executor agar tidak blocking event loop.
    """
    try:
        ticker_symbol = f"{stock_code}.JK"
        ticker = yf.Ticker(ticker_symbol)

        # Ambil 14 bulan supaya ada cukup data untuk 1y perbandingan
        hist = ticker.history(period="14mo", auto_adjust=True)

        if hist.empty:
            return {"available": False, "error": f"Tidak ada data harga untuk {ticker_symbol}"}

        # Harga sekarang
        current_price = float(hist["Close"].iloc[-1])
        last_date = hist.index[-1]

        def price_n_days_ago(n: int) -> float | None:
            """Harga n kalender-hari yang lalu (cari hari trading terdekat)."""
            target = last_date - timedelta(days=n)
            subset = hist[hist.index <= target]
            if subset.empty:
                return None
            return float(subset["Close"].iloc[-1])

        def pct(old: float | None, new: float) -> float | None:
            if old and old != 0:
                return round((new - old) / old * 100, 2)
            return None

        price_1m  = price_n_days_ago(30)
        price_6m  = price_n_days_ago(180)
        price_1y  = price_n_days_ago(365)

        chg_1m = pct(price_1m, current_price)
        chg_6m = pct(price_6m, current_price)
        chg_1y = pct(price_1y, current_price)

        result: dict = {
            "available":      True,
            "ticker":         ticker_symbol,
            "current_price":  round(current_price),
            "currency":       "IDR",
            "last_date":      last_date.strftime("%d %b %Y"),
            "price_1m_ago":   round(price_1m)  if price_1m  else None,
            "price_6m_ago":   round(price_6m)  if price_6m  else None,
            "price_1y_ago":   round(price_1y)  if price_1y  else None,
            "change_1m_pct":  chg_1m,
            "change_6m_pct":  chg_6m,
            "change_1y_pct":  chg_1y,
        }

        # Data tambahan (opsional)
        try:
            info = ticker.info
            result["market_cap"] = info.get("marketCap")
            result["volume"]     = info.get("volume")
            result["pe_ratio"]   = info.get("trailingPE")
            result["pb_ratio"]   = info.get("priceToBook")
            result["div_yield"]  = info.get("dividendYield")
        except Exception:
            pass

        return result

    except Exception as e:
        return {"available": False, "error": str(e)}


async def fetch_price_data(stock_code: str) -> dict:
    """Async wrapper untuk fetching harga (non-blocking)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _fetch_price_sync, stock_code)
