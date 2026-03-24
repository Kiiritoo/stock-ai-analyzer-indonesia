"""
services/news_fetcher.py — Fetch & rank news articles for IDX stocks.
Adapted from local app's news_fetcher.py (identical logic, same output format).
"""
import re
from datetime import datetime, timezone
from typing import Optional

import feedparser
import httpx

# ── Stock → Company name map ──────────────────────────────────────────────────
STOCK_COMPANY_MAP: dict[str, list[str]] = {
    "BBCA": ["Bank Central Asia", "BCA"],
    "BBRI": ["Bank Rakyat Indonesia", "BRI"],
    "BMRI": ["Bank Mandiri", "Mandiri"],
    "BBNI": ["Bank Negara Indonesia", "BNI"],
    "TLKM": ["Telkom", "Telekomunikasi Indonesia"],
    "ASII": ["Astra International", "Astra"],
    "UNVR": ["Unilever Indonesia", "Unilever"],
    "GOTO": ["GoTo", "Tokopedia", "Gojek"],
    "BUMI": ["Bumi Resources", "Bumi"],
    "ANTM": ["Aneka Tambang", "ANTAM"],
    "INDF": ["Indofood", "Indofood CBP"],
    "ICBP": ["Indofood CBP"],
    "KLBF": ["Kalbe Farma", "Kalbe"],
    "PGAS": ["Perusahaan Gas Negara", "PGN"],
    "ADRO": ["Adaro Energy", "Adaro"],
    "PTBA": ["Bukit Asam"],
    "SMGR": ["Semen Indonesia", "Semen Gresik"],
    "HMSP": ["HM Sampoerna", "Sampoerna"],
    "GGRM": ["Gudang Garam"],
    "UNTR": ["United Tractors"],
    "WSKT": ["Waskita Karya", "Waskita"],
    "PTPP": ["PP Persero"],
    "WIKA": ["Wijaya Karya"],
    "JSMR": ["Jasa Marga"],
    "BSDE": ["Bumi Serpong Damai", "BSD City"],
    "CPIN": ["Charoen Pokphand Indonesia", "Charoen Pokphand"],
    "MAPI": ["Mitra Adiperkasa"],
    "MEDC": ["Medco Energi", "Medco"],
    "INCO": ["Vale Indonesia", "INCO"],
    "EMTK": ["Elang Mahkota Teknologi"],
    "ACES": ["Ace Hardware Indonesia", "Ace Hardware"],
    "SIDO": ["Sido Muncul", "Industri Jamu"],
    "ERAA": ["Erajaya Swasembada", "Erajaya"],
    "MNCN": ["Media Nusantara Citra", "MNC"],
    "INKP": ["Indah Kiat Pulp", "Indah Kiat"],
    "TKIM": ["Tjiwi Kimia"],
    "BRPT": ["Barito Pacific", "Barito"],
    "MDKA": ["Merdeka Copper Gold", "Merdeka"],
    "AMMN": ["Amman Mineral", "Amman"],
    "ITMG": ["Indo Tambangraya Megah"],
    "HRUM": ["Harum Energy"],
    "TPIA": ["Chandra Asri Petrochemical", "Chandra Asri"],
    "BNGA": ["Bank CIMB Niaga", "CIMB Niaga"],
    "BTPS": ["Bank BTPN Syariah", "BTPN Syariah"],
    "ARTO": ["Bank Jago"],
    "BRIS": ["Bank Syariah Indonesia", "BSI"],
    "AGRO": ["Bank Raya", "BRI Agro"],
    "MIKA": ["Mitra Keluarga", "RS Mitra Keluarga"],
    "HEAL": ["Medikaloka Hermina", "RS Hermina"],
    "SRTG": ["Saratoga Investama", "Saratoga"],
    "MYOR": ["Mayora Indah", "Mayora"],
    "ULTJ": ["Ultra Jaya", "Ultrajaya"],
    "DNET": ["Indomaret", "Indoritel Makmur"],
    "RANC": ["Ranch Market"],
}

# ── Article categories (same weights as local app) ────────────────────────────
ARTICLE_CATEGORIES = [
    {"id": "corporate_action",      "label": "Corporate Action",    "color": "#fb923c", "weight": 1.8,
     "keywords": ["akuisisi","merger","rights issue","right issue","buyback","obligasi","bond",
                  "IPO","delisting","spin-off","divestasi","tender offer","RUPST","RUPS",
                  "kuasi reorganisasi","restrukturisasi","refinancing","dividen","dividend",
                  "stock split","reverse split","ekspansi","pabrik baru","proyek baru",
                  "kontrak","perjanjian","MOU","memorandum"]},
    {"id": "fundamental",           "label": "Laporan Keuangan",    "color": "#34d399", "weight": 1.6,
     "keywords": ["laba","rugi","revenue","pendapatan","penjualan","laporan keuangan",
                  "laporan tahunan","laporan semester","kuartal","semester","Q1","Q2","Q3","Q4",
                  "EPS","ROE","ROA","NIM","CAR","NPL","margin","EBITDA","cash flow","arus kas",
                  "kinerja keuangan","pertumbuhan laba","penurunan laba"]},
    {"id": "ownership",             "label": "Smart Money",         "color": "#a78bfa", "weight": 1.5,
     "keywords": ["penjualan saham","beli saham","fund manager","pemegang saham","kepemilikan",
                  "insider","asing jual","asing beli","foreign sell","foreign buy","portofolio",
                  "divestasi","menyerok","akumulasi","BlackRock","Vanguard","Fidelity"]},
    {"id": "sector_macro",          "label": "Sektor/Makro",        "color": "#38bdf8", "weight": 1.2,
     "keywords": ["batubara","nikel","emas","tembaga","timah","minyak","CPO","sawit","komoditas",
                  "suku bunga","inflasi","rupiah","dolar","fed rate","transisi energi","EV",
                  "IHSG","BI rate","OJK","pemerintah","regulasi"]},
    {"id": "analyst_recommendation","label": "Rekomendasi Analis",  "color": "#fbbf24", "weight": 1.0,
     "keywords": ["rekomendasi","target harga","target price","beli","jual","tahan","hold",
                  "buy","sell","potensi naik","potensi turun","upgrade","downgrade","analis",
                  "sekuritas","riset"]},
    {"id": "market_noise",          "label": "Pergerakan Pasar",    "color": "#6b7280", "weight": 0.7,
     "keywords": ["IHSG naik","IHSG turun","pergerakan harian","top gainers","top losers",
                  "saham paling aktif","market wrap","closing","pembukaan pasar"]},
]

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(entry) -> tuple[str, Optional[datetime]]:
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.strftime("%d %b %Y, %H:%M"), dt
    except Exception:
        pass
    return "Tanggal tidak diketahui", None


def detect_category(title: str, summary: str) -> dict:
    text = (title + " " + summary).lower()
    for cat in ARTICLE_CATEGORIES:
        if any(kw.lower() in text for kw in cat["keywords"]):
            return cat
    return {"id": "general", "label": "Umum", "color": "#6b7280", "weight": 0.9}


def score_article(title: str, summary: str, keywords: list[str]) -> tuple[float, dict]:
    text_full   = (title + " " + summary).lower()
    title_lower = title.lower()
    stock_code  = keywords[0]
    kw_in_title  = sum(1 for kw in keywords if kw.lower() in title_lower)
    kw_score_t   = min(1.0, kw_in_title / max(1, len(keywords))) * 0.4
    kw_in_body   = sum(1 for kw in keywords if kw.lower() in text_full)
    kw_score_b   = min(1.0, kw_in_body / max(1, len(keywords))) * 0.2
    exact_bonus  = 0.2 if re.search(r'\b' + re.escape(stock_code) + r'\b', title) else 0.0
    category     = detect_category(title, summary)
    final_score  = round((kw_score_t + kw_score_b + exact_bonus) * category["weight"], 3)
    return final_score, category


# ── Feed builders ─────────────────────────────────────────────────────────────
def build_google_news_feeds(stock_code: str, company_names: list[str]) -> list[tuple[str, str]]:
    base = "https://news.google.com/rss/search?hl=id&gl=ID&ceid=ID:id&q="
    c  = company_names[0] if company_names else stock_code
    c2 = company_names[1] if len(company_names) > 1 else ""
    feeds = [
        (f"GNews: {stock_code} saham",   base + f"{stock_code}+saham+Indonesia"),
        (f"GNews: {c} BEI",              base + f"{c.replace(' ', '+')}+BEI+saham"),
        (f"GNews: {stock_code} kinerja", base + f"{stock_code}+kinerja+laporan+keuangan"),
        (f"GNews: {c} investasi",        base + f"{c.replace(' ', '+')}+investasi+rekomendasi"),
        (f"GNews: {stock_code} akuisisi",base + f"{stock_code}+akuisisi+obligasi+rights+issue"),
    ]
    if c2:
        feeds.append((f"GNews: {c2}", base + f"{c2.replace(' ', '+')}+saham+IDX"))
    return feeds


def build_general_feeds() -> list[tuple[str, str]]:
    return [
        ("CNBC Indonesia Market", "https://www.cnbcindonesia.com/market/rss"),
        ("Detik Finance",         "https://finance.detik.com/rss"),
    ]


# ── Core fetch ────────────────────────────────────────────────────────────────
async def _fetch_from_feed(
    client: httpx.AsyncClient,
    source_name: str,
    feed_url: str,
    keywords: list[str],
    seen_urls: set,
) -> list[dict]:
    try:
        resp = await client.get(feed_url)
        if resp.status_code != 200:
            return []
        feed    = feedparser.parse(resp.text)
        results = []
        for entry in feed.entries[:60]:
            title   = entry.get("title", "").strip()
            summary = clean_html(entry.get("summary", entry.get("description", "")))
            url     = entry.get("link", "")
            if not title or not url or url in seen_urls:
                continue
            if not any(kw.lower() in f"{title} {summary}".lower() for kw in keywords):
                continue
            seen_urls.add(url)
            date_str, date_obj = parse_date(entry)
            rel_score, category = score_article(title, summary, keywords)
            results.append({
                "title":          title,
                "summary":        summary[:600],
                "url":            url,
                "source":         source_name,
                "published":      date_str,
                "category":       category["id"],
                "category_label": category["label"],
                "category_color": category["color"],
                "_date_obj":      date_obj,
                "_score":         rel_score,
            })
        return results
    except Exception:
        return []


async def fetch_articles(
    stock_code: str,
    max_display: int = 40,
    max_ai: int = 15,
) -> tuple[list[dict], list[dict]]:
    """
    Returns (display_articles, ai_articles).
    Articles sorted by relevance + recency (same algorithm as local app).
    """
    code          = stock_code.upper().strip()
    company_names = STOCK_COMPANY_MAP.get(code, [])
    keywords      = [code] + company_names
    seen_urls: set[str] = set()
    all_articles: list[dict] = []

    google_feeds  = build_google_news_feeds(code, company_names)
    general_feeds = build_general_feeds()

    async with httpx.AsyncClient(timeout=15.0, headers=REQUEST_HEADERS, follow_redirects=True) as client:
        for name, url in google_feeds + general_feeds:
            articles = await _fetch_from_feed(client, name, url, keywords, seen_urls)
            all_articles.extend(articles)

    def sort_key(a: dict) -> float:
        score    = a.get("_score", 0.0)
        date_obj = a.get("_date_obj")
        if date_obj:
            age_h = max(0, (datetime.now(tz=timezone.utc) - date_obj).total_seconds() / 3600)
            return score + max(0.0, 0.15 * (1 - age_h / 72))
        return score

    all_articles.sort(key=sort_key, reverse=True)

    def clean(art: dict) -> dict:
        return {k: v for k, v in art.items() if not k.startswith("_")}

    return [clean(a) for a in all_articles[:max_display]], [clean(a) for a in all_articles[:max_ai]]
