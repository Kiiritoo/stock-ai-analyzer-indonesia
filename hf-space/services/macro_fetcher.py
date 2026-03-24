"""
services/macro_fetcher.py — Fetch macroeconomic data for Indonesia.
Ported from local app — identical logic and output format.
"""
import asyncio
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

import feedparser
import httpx
import yfinance as yf

_executor = ThreadPoolExecutor(max_workers=4)

async def _run(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn, *args)


# ── Market prices ─────────────────────────────────────────────────────────────
TICKERS = {
    "IHSG":    [("^JKSE",    "IHSG",          "")],
    "USD_IDR": [("USDIDR=X", "USD/IDR",       "IDR"), ("IDR=X", "USD/IDR", "IDR")],
    "Gold":    [("GC=F",     "Emas",          "USD/oz")],
    "Oil":     [("CL=F",     "Minyak WTI",    "USD/bbl"), ("BZ=F", "Minyak Brent", "USD/bbl")],
    "Nickel":  [("NI=F",     "Nikel LME",     "USD/t"),   ("INCO.JK", "Nikel (INCO)", "IDR"), ("VALE", "Nikel (VALE)", "USD")],
    "Coal":    [("BTU",      "Batu Bara",     "USD"),     ("COAL.L", "Batu Bara ETF", "GBp")],
}

def _fetch_one_ticker(sym: str, label: str, unit: str) -> Optional[dict]:
    try:
        hist = yf.Ticker(sym).history(period="5d", auto_adjust=True)
        if hist.empty:
            return None
        now  = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else now
        chg  = round((now - prev) / prev * 100, 2) if prev else 0.0
        return {
            "available": True, "label": label, "unit": unit,
            "price":      round(now, 2),
            "change_pct": chg,
            "direction":  "UP" if chg > 0.05 else ("DOWN" if chg < -0.05 else "FLAT"),
            "date":       hist.index[-1].strftime("%d %b %Y"),
        }
    except Exception:
        return None

def _fetch_market_sync() -> dict:
    result: dict = {}
    for key, alts in TICKERS.items():
        for sym, label, unit in alts:
            data = _fetch_one_ticker(sym, label, unit)
            if data:
                result[key] = data
                break
        if key not in result:
            result[key] = {"available": False, "label": alts[0][1]}
    return result

async def fetch_market_data() -> dict:
    return await _run(_fetch_market_sync)


# ── BI Rate ───────────────────────────────────────────────────────────────────
_BI_FALLBACK = {"available": True, "rate": 5.75, "source": "Fallback — cek bi.go.id", "date": ""}

def _fetch_bi_rate_sync() -> dict:
    # Try 1: BI Website
    try:
        req = urllib.request.Request(
            "https://www.bi.go.id/id/statistik/indikator/data-bi-rate.aspx",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="ignore")
        matches = re.findall(r'(\d+[,.]\d+)\s*%', html)
        for m in matches:
            rate = float(m.replace(",", "."))
            if 1.5 <= rate <= 15:
                return {"available": True, "rate": rate, "source": "BI Website"}
    except Exception:
        pass
    # Try 2: Google News
    for feed_url in [
        "https://news.google.com/rss/search?q=%22BI+Rate%22+%22persen%22&hl=id&gl=ID&ceid=ID:id",
        "https://news.google.com/rss/search?q=suku+bunga+acuan+Bank+Indonesia&hl=id&gl=ID&ceid=ID:id",
    ]:
        try:
            feed = feedparser.parse(feed_url)
            for e in feed.entries[:8]:
                m = re.search(r'(\d+[,.]\d+)\s*(?:persen|%)', e.get("title", ""), re.I)
                if m:
                    rate = float(m.group(1).replace(",", "."))
                    if 1.5 <= rate <= 12:
                        return {"available": True, "rate": rate, "source": "Google News",
                                "date": e.get("published", "")[:16]}
        except Exception:
            pass
    return _BI_FALLBACK

async def fetch_bi_rate() -> dict:
    return await _run(_fetch_bi_rate_sync)


# ── Kurs Pajak ────────────────────────────────────────────────────────────────
def _fetch_kurs_pajak_sync() -> dict:
    # Strategy 1: Kemenkeu API
    for url in ["https://fiskal.kemenkeu.go.id/api/kurs-pajak",
                "https://fiskal.kemenkeu.go.id/api/v1/kurs-pajak"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                import json
                data = json.loads(r.read().decode())
                for m in re.findall(r'(?:USD|usd)[^0-9]{0,30}?(1[0-9][\d]{3})', str(data)):
                    rate = float(m)
                    if 10000 < rate < 25000:
                        return {"available": True, "rate": rate, "source": "Kemenkeu API", "official": True}
        except Exception:
            pass
    # Strategy 2: Fallback to USD/IDR market
    try:
        hist = yf.Ticker("USDIDR=X").history(period="2d", auto_adjust=True)
        if not hist.empty:
            rate = round(float(hist["Close"].iloc[-1]), 0)
            return {"available": True, "rate": rate, "source": "Kurs Pasar", "official": False,
                    "note": "KMK resmi belum berhasil diambil. Menggunakan kurs pasar sebagai referensi."}
    except Exception:
        pass
    return {"available": False, "note": "Data tidak tersedia"}

async def fetch_kurs_pajak() -> dict:
    return await _run(_fetch_kurs_pajak_sync)


# ── Fed Rate ──────────────────────────────────────────────────────────────────
def _fetch_fred_series(series_id: str) -> Optional[dict]:
    try:
        req = urllib.request.Request(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*",
                     "Referer": "https://fred.stlouisfed.org/"})
        with urllib.request.urlopen(req, timeout=15) as r:
            lines = [l for l in r.read().decode().splitlines() if l and not l.startswith("DATE")]
        for line in reversed(lines):
            parts = line.strip().split(",")
            if len(parts) >= 2 and parts[1] not in [".", ""]:
                return {"rate": float(parts[1]), "date": parts[0]}
    except Exception:
        pass
    return None

def _fetch_fed_rate_sync() -> dict:
    data = _fetch_fred_series("DFEDTARU")
    if data:
        return {"available": True, "rate": data["rate"], "date": data["date"],
                "source": "FRED (Target Atas)", "series": "DFEDTARU"}
    data = _fetch_fred_series("FEDFUNDS")
    if data:
        return {"available": True, "rate": data["rate"], "date": data["date"],
                "source": "FRED (Effective)", "series": "FEDFUNDS"}
    return {"available": True, "rate": 4.33, "date": "est. 2025",
            "source": "Fallback — cek federalreserve.gov", "series": "fallback"}

async def fetch_fed_rate() -> dict:
    return await _run(_fetch_fed_rate_sync)


# ── Global News ───────────────────────────────────────────────────────────────
_GLOBAL_FEEDS = [
    ("The Fed",      "https://news.google.com/rss/search?q=Federal+Reserve+rate+decision&hl=en&gl=US&ceid=US:en"),
    ("China Ekonomi","https://news.google.com/rss/search?q=China+economy+GDP&hl=en&gl=US&ceid=US:en"),
    ("IHSG Rupiah",  "https://news.google.com/rss/search?q=IHSG+rupiah+investor+asing&hl=id&gl=ID&ceid=ID:id"),
    ("Komoditas RI", "https://news.google.com/rss/search?q=harga+batu+bara+nikel+Indonesia&hl=id&gl=ID&ceid=ID:id"),
]

async def fetch_global_news() -> list:
    articles = []
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True,
                                  headers={"User-Agent": "Mozilla/5.0"}) as client:
        for source, url in _GLOBAL_FEEDS:
            try:
                resp = await client.get(url)
                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:3]:
                    title = entry.get("title", "").strip()
                    if title:
                        articles.append({"title": title, "source": source, "url": entry.get("link", "")})
            except Exception:
                pass
    return articles


# ── Combined ──────────────────────────────────────────────────────────────────
async def fetch_all_macro() -> dict:
    """Fetch all macro data in parallel."""
    market, bi_rate, kurs_pajak, fed_rate, global_news = await asyncio.gather(
        fetch_market_data(),
        fetch_bi_rate(),
        fetch_kurs_pajak(),
        fetch_fed_rate(),
        fetch_global_news(),
    )
    return {
        "market":      market,
        "bi_rate":     bi_rate,
        "kurs_pajak":  kurs_pajak,
        "fed_rate":    fed_rate,
        "global_news": global_news,
        "updated_at":  datetime.now().strftime("%d/%m/%Y %H:%M:%S WIB"),
    }


def build_macro_context(macro: dict, stock_code: str) -> str:
    m    = macro.get("market", {})
    bi   = macro.get("bi_rate", {})
    fed  = macro.get("fed_rate", {})
    usd  = m.get("USD_IDR", {})
    ihsg = m.get("IHSG", {})
    bi_r   = bi.get("rate", "?")
    fed_r  = fed.get("rate", "?")
    usd_p  = usd.get("price", 0)
    usd_c  = usd.get("change_pct", 0)
    ihsg_c = ihsg.get("change_pct", 0)
    rupiah = "MELEMAH" if usd_c > 0.5 else ("MENGUAT" if usd_c < -0.5 else "STABIL")
    ihsg_s = "NAIK" if ihsg_c > 0.3 else ("TURUN" if ihsg_c < -0.3 else "SIDEWAYS")
    return (
        f"\nKONTEKS MAKRO (pertimbangkan untuk {stock_code}):\n"
        f"BI Rate: {bi_r}% | Fed Rate: {fed_r}% | "
        f"USD/IDR: {usd_p:,.0f} ({rupiah}, {usd_c:+.2f}%) | "
        f"IHSG: {ihsg_s} ({ihsg_c:+.2f}%)\n"
        f"→ Faktor ini mempengaruhi cost of capital, psikologi investor asing, dan valuasi {stock_code}.\n"
    )
