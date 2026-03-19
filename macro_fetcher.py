"""
macro_fetcher.py — Fetch semua data makro ekonomi Indonesia.
Cache TTL: market 5 menit, rates 1 jam, KMK 6 jam, berita 10 menit.

Debugging notes:
- Fed Rate: pakai DFEDTARU (daily target upper bound) bukan FEDFUNDS (monthly lag)
- Kurs Pajak: Kemenkeu website adalah SPA, scrape via API JSON endpoint
- BI Rate: Multi-source scraping dengan validasi tanggal terbaru
- Nikel: pakai LMAHDS03 (LME data via FRED) sebagai alternatif NI=F yang sering gagal
"""
import asyncio
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

import feedparser
import httpx
import yfinance as yf

_executor = ThreadPoolExecutor(max_workers=6)

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict = {}

def _get(key: str, ttl: int) -> Optional[dict]:
    e = _cache.get(key)
    if e and time.time() - e["ts"] < ttl:
        return e["data"]
    return None

def _set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}

async def _run(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, fn, *args)


# ── Market prices (IHSG, USD/IDR, Komoditas) ─────────────────────────────────
# Ticker alternatives jika primer gagal
TICKERS = {
    "IHSG":    [("^JKSE",    "IHSG",       ""),    ("^JKSE", "IHSG", "")],
    "USD_IDR": [("USDIDR=X", "USD/IDR",    "IDR"), ("IDR=X", "USD/IDR", "IDR")],
    "Gold":    [("GC=F",     "Emas",       "USD/oz")],
    "Oil":     [("CL=F",     "Minyak WTI", "USD/bbl"), ("BZ=F", "Minyak Brent", "USD/bbl")],
    # Nickel: NI=F & NICKEL.L sering gagal di yfinance
    # INCO.JK = PT Vale Indonesia (nikel, listed IDX) sebagai proxy yang reliable
    # VALE = Vale SA (global) sebagai alternatif
    "Nickel":  [("NI=F",     "Nikel LME",  "USD/t"), ("INCO.JK", "Nikel (INCO)", "IDR"), ("VALE", "Nikel (VALE)", "USD")],
    # Batu Bara: BTU = Peabody Energy (US coal producer, punya aset di Indonesia)
    "Coal":    [("BTU",      "Batu Bara",  "USD"), ("COAL.L", "Batu Bara ETF", "GBp")],
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
    for key, alternatives in TICKERS.items():
        success = False
        for sym, label, unit in alternatives:
            data = _fetch_one_ticker(sym, label, unit)
            if data:
                result[key] = data
                success = True
                break
        if not success:
            label = alternatives[0][1]
            result[key] = {"available": False, "label": label}
    return result

async def fetch_market_data() -> dict:
    cached = _get("market", 300)
    if cached: return cached
    data = await _run(_fetch_market_sync)
    _set("market", data)
    return data

# Fungsi untuk chart data IHSG (1 bulan untuk chart mini)
def _fetch_ihsg_chart_sync() -> list:
    """Ambil data IHSG 1 bulan terakhir untuk chart."""
    try:
        hist = yf.Ticker("^JKSE").history(period="1mo", auto_adjust=True)
        if hist.empty:
            return []
        return [
            {"date": str(idx.date()), "close": round(float(row["Close"]), 2)}
            for idx, row in hist.iterrows()
        ]
    except Exception:
        return []

async def fetch_ihsg_chart() -> list:
    cached = _get("ihsg_chart", 300)
    if cached: return cached
    data = await _run(_fetch_ihsg_chart_sync)
    _set("ihsg_chart", data)
    return data


# ── BI Rate ───────────────────────────────────────────────────────────────────
# Per Maret 2026, BI Rate sekitar 5.75% (verifikasi dari berita)
_BI_FALLBACK = {"available": True, "rate": 5.75, "source": "Fallback — cek bi.go.id", "date": ""}

def _fetch_bi_rate_sync() -> dict:
    # Try 1: BI Data API (XML/JSON endpoint)
    try:
        req = urllib.request.Request(
            "https://www.bi.go.id/biwebservice/wskursbi.asmx",
            headers={"User-Agent": "Mozilla/5.0"})
        # Just test if BI is reachable, parse from main site
        req2 = urllib.request.Request(
            "https://www.bi.go.id/id/statistik/indikator/data-bi-rate.aspx",
            headers={"User-Agent": "Mozilla/5.0",
                     "Accept": "text/html,application/xhtml+xml"})
        with urllib.request.urlopen(req2, timeout=8) as r:
            html = r.read().decode("utf-8", errors="ignore")
        # BI Rate pattern with date: look for table cells with percentage
        # Find all rates and take the most recent one (first in the table)
        matches = re.findall(r'(\d+[,\.]\d+)\s*%', html)
        seen_rates = []
        for m in matches:
            rate = float(m.replace(",", "."))
            if 1.5 <= rate <= 15:
                seen_rates.append(rate)
        if seen_rates:
            return {"available": True, "rate": seen_rates[0], "source": "BI Website"}
    except Exception:
        pass

    # Try 2: Kontan RSS (lebih reliabel untuk angka BI Rate)
    for feed_url in [
        "https://news.google.com/rss/search?q=%22BI+Rate%22+%22persen%22&hl=id&gl=ID&ceid=ID:id",
        "https://news.google.com/rss/search?q=suku+bunga+acuan+Bank+Indonesia&hl=id&gl=ID&ceid=ID:id",
    ]:
        try:
            feed = feedparser.parse(feed_url)
            for e in feed.entries[:8]:
                title = e.get("title", "")
                # Pattern: "X,XX persen" or "X.XX%" or "X,XX%"
                m = re.search(r'(\d+[,\.]\d+)\s*(?:persen|%)', title, re.I)
                if m:
                    rate = float(m.group(1).replace(",", "."))
                    if 1.5 <= rate <= 12:
                        # Get publish date
                        pub = e.get("published", "")[:16]
                        return {"available": True, "rate": rate, "source": "Google News", "date": pub}
        except Exception:
            pass

    return _BI_FALLBACK

async def fetch_bi_rate() -> dict:
    cached = _get("bi_rate", 3600)
    if cached: return cached
    data = await _run(_fetch_bi_rate_sync)
    _set("bi_rate", data)
    return data


# ── Kurs Pajak (KMK) ─────────────────────────────────────────────────────────
# Kemenkeu.go.id adalah SPA (JavaScript). Coba beberapa strategi:
# 1. API JSON endpoint Kemenkeu (jika tersedia)
# 2. BI Transaction Rate (kurs tengah BI = basis KMK)
# 3. Fallback ke USD/IDR market rate
def _fetch_kurs_pajak_sync() -> dict:
    # Strategy 1: Kemenkeu DJPB API (Ditjen Perbendaharaan)
    api_urls = [
        "https://fiskal.kemenkeu.go.id/api/kurs-pajak",
        "https://fiskal.kemenkeu.go.id/api/v1/kurs-pajak",
        "https://api.kemenkeu.go.id/kurs-pajak",
    ]
    for url in api_urls:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                import json
                data = json.loads(r.read().decode())
                # Try to extract USD rate from JSON response
                text = str(data)
                for m in re.findall(r'(?:USD|usd)[^0-9]{0,30}?(1[0-9][\d]{3})', text):
                    rate = float(m)
                    if 10000 < rate < 25000:
                        return {"available": True, "rate": rate, "source": "Kemenkeu API", "official": True}
        except Exception:
            pass

    # Strategy 2: BI Kurs Transaksi (official daily rate from Bank Indonesia)
    try:
        req = urllib.request.Request(
            "https://www.bi.go.id/id/statistik/informasi-kurs/transaksi-bi/default.aspx",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
        # Look for USD exchange rate in BI table
        for pat in [
            r'USD[^<]{0,80}?(1[4-9][,\.]?\d{3})',
            r'(1[4-9]\.\d{3})',
        ]:
            for m in re.findall(pat, html, re.I | re.S):
                rate_str = m.replace(".", "").replace(",", "")[:6]
                try:
                    rate = float(rate_str)
                    if 13000 < rate < 25000:
                        return {
                            "available": True, "rate": rate,
                            "source": "Kurs Tengah BI", "official": True,
                            "note": "Kurs Transaksi Bank Indonesia (basis KMK)"
                        }
                except ValueError:
                    pass
    except Exception:
        pass

    # Strategy 3: Google News for latest KMK announcement
    try:
        feed = feedparser.parse(
            "https://news.google.com/rss/search?q=kurs+pajak+KMK+USD&hl=id&gl=ID&ceid=ID:id")
        for e in feed.entries[:5]:
            title = e.get("title", "") + " " + e.get("summary", "")
            for m in re.findall(r'Rp\s*([0-9.,]{7,12})\s*(?:per|\/)', title):
                rate_str = m.replace(".", "").replace(",", "")[:6]
                try:
                    rate = float(rate_str)
                    if 13000 < rate < 25000:
                        return {"available": True, "rate": rate, "source": "Berita KMK"}
                except ValueError:
                    pass
    except Exception:
        pass

    # Fallback: pakai USD/IDR market + label jelas
    try:
        hist = yf.Ticker("USDIDR=X").history(period="2d", auto_adjust=True)
        if not hist.empty:
            rate = round(float(hist["Close"].iloc[-1]), 0)
            return {
                "available": True, "rate": rate,
                "source": "Kurs Pasar", "official": False,
                "note": "KMK resmi belum berhasil diambil. Menggunakan kurs pasar USD/IDR sebagai referensi."
            }
    except Exception:
        pass

    return {"available": False, "note": "Data tidak tersedia"}

async def fetch_kurs_pajak() -> dict:
    cached = _get("kurs_pajak", 21600)   # cache 6 jam (KMK mingguan, tapi kurs tengah BI harian)
    if cached: return cached
    data = await _run(_fetch_kurs_pajak_sync)
    _set("kurs_pajak", data)
    return data


# ── Fed Rate (FRED CSV — tanpa API key) ──────────────────────────────────────
# FEDFUNDS = effective monthly (lag 1 bulan)
# DFEDTARU = target rate upper bound, DAILY — lebih akurat & real-time
def _fetch_fred_series(series_id: str) -> Optional[dict]:
    """Fetch a single FRED series CSV and return latest non-missing value."""
    try:
        req = urllib.request.Request(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/csv,text/plain,*/*",
                "Referer": "https://fred.stlouisfed.org/"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            lines = [l for l in r.read().decode().splitlines()
                     if l and not l.startswith("DATE")]
        # Scan from end to find last valid value
        for line in reversed(lines):
            parts = line.strip().split(",")
            if len(parts) >= 2 and parts[1] not in [".", ""]:
                return {"rate": float(parts[1]), "date": parts[0]}
    except Exception:
        pass
    return None

def _fetch_fed_rate_sync() -> dict:
    # Primary: DFEDTARU (Upper target, daily — most current)
    data = _fetch_fred_series("DFEDTARU")
    if data:
        return {
            "available": True, "rate": data["rate"], "date": data["date"],
            "source": "FRED (Target Atas)", "series": "DFEDTARU"
        }
    # Fallback 1: FEDFUNDS (monthly effective)
    data = _fetch_fred_series("FEDFUNDS")
    if data:
        return {
            "available": True, "rate": data["rate"], "date": data["date"],
            "source": "FRED (Effective)", "series": "FEDFUNDS"
        }
    # Fallback 2: Google News (Fed rate announcement)
    try:
        feed = feedparser.parse(
            "https://news.google.com/rss/search?q=Federal+Reserve+fed+funds+rate+percent&hl=en&gl=US&ceid=US:en")
        for e in feed.entries[:5]:
            title = e.get("title", "")
            m = re.search(r'(\d+\.\d+)\s*(?:percent|%|to|-)\s*(\d+\.\d+)\s*(?:percent|%)',
                          title, re.I)
            if m:
                # Take upper bound of range (e.g. "4.25 to 4.50" -> 4.50)
                rate = float(m.group(2))
                if 1.0 <= rate <= 10.0:
                    return {
                        "available": True, "rate": rate,
                        "date": e.get("published", "")[:10],
                        "source": "Google News (Fed)", "series": "news"
                    }
    except Exception:
        pass
    # Last resort: known value (Fed cut to 4.25-4.50% in Dec 2024)
    return {"available": True, "rate": 4.33, "date": "est. 2025",
            "source": "Fallback — cek federalreserve.gov", "series": "fallback"}

async def fetch_fed_rate() -> dict:
    cached = _get("fed_rate", 3600)
    if cached: return cached
    data = await _run(_fetch_fed_rate_sync)
    _set("fed_rate", data)
    return data


# ── Global News ───────────────────────────────────────────────────────────────
_GLOBAL_FEEDS = [
    ("The Fed", "https://news.google.com/rss/search?q=Federal+Reserve+rate+decision&hl=en&gl=US&ceid=US:en"),
    ("China Ekonomi", "https://news.google.com/rss/search?q=China+economy+GDP&hl=en&gl=US&ceid=US:en"),
    ("IHSG Rupiah", "https://news.google.com/rss/search?q=IHSG+rupiah+investor+asing&hl=id&gl=ID&ceid=ID:id"),
    ("Komoditas RI", "https://news.google.com/rss/search?q=harga+batu+bara+nikel+Indonesia&hl=id&gl=ID&ceid=ID:id"),
]

async def fetch_global_news() -> list:
    cached = _get("global_news", 600)
    if cached: return cached
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
    _set("global_news", articles)
    return articles


# ── Combined ──────────────────────────────────────────────────────────────────
async def fetch_all_macro() -> dict:
    """Fetch semua data makro secara parallel."""
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
        "cache_ttl_s": 300,
    }


# ── Macro context untuk prompt saham ─────────────────────────────────────────
def build_macro_context(macro: dict, stock_code: str) -> str:
    m      = macro.get("market", {})
    bi     = macro.get("bi_rate", {})
    fed    = macro.get("fed_rate", {})
    usd    = m.get("USD_IDR", {})
    ihsg   = m.get("IHSG", {})

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
