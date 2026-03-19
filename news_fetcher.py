import feedparser
import httpx
import re
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Mapping kode saham → nama-nama perusahaan
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Kategori artikel dengan bobot skor & label
# Urutan prioritas: Corporate Action > Fundamental > Ownership > Sektor > Rekomendasi > Noise
# ---------------------------------------------------------------------------
ARTICLE_CATEGORIES = [
    {
        "id": "corporate_action",
        "label": "Corporate Action",
        "color": "#fb923c",              # orange
        "weight": 1.8,                   # bobot paling tinggi
        "keywords": [
            "akuisisi", "merger", "rights issue", "right issue", "buyback",
            "obligasi", "bond", "IPO", "delisting", "spin-off", "divestasi",
            "tender offer", "RUPST", "RUPS", "reshuffled", "pengambilalihan",
            "kuasi reorganisasi", "restrukturisasi", "refinancing",
            "dividen", "dividend", "stock split", "reverse split",
            "tambang", "ekspansi", "pabrik baru", "proyek baru",
            "kontrak", "perjanjian", "MOU", "memorandum",
        ],
    },
    {
        "id": "fundamental",
        "label": "Laporan Keuangan",
        "color": "#34d399",              # green
        "weight": 1.6,
        "keywords": [
            "laba", "rugi", "revenue", "pendapatan", "penjualan",
            "laporan keuangan", "laporan tahunan", "laporan semester",
            "kuartal", "semester", "Q1", "Q2", "Q3", "Q4",
            "EPS", "ROE", "ROA", "NIM", "CAR", "NPL",
            "margin", "EBITDA", "cash flow", "arus kas",
            "kinerja keuangan", "pertumbuhan laba", "penurunan laba",
            "naik 25%", "turun 15%",   # pola angka persen setelah laba
        ],
    },
    {
        "id": "ownership",
        "label": "Smart Money",
        "color": "#a78bfa",              # purple
        "weight": 1.5,
        "keywords": [
            "penjualan saham", "beli saham", "fund manager",
            "pemegang saham", "kepemilikan", "insider",
            "asing jual", "asing beli", "foreign sell", "foreign buy",
            "portofolio", "divestasi", "menyerok", "akumulasi",
            "UBS", "Chengdong", "Fidelity", "BlackRock", "Vanguard",
            "pemegang saham pengendali", "direksi jual", "komisaris",
        ],
    },
    {
        "id": "sector_macro",
        "label": "Sektor/Makro",
        "color": "#38bdf8",              # cyan
        "weight": 1.2,
        "keywords": [
            "batubara", "nikel", "emas", "tembaga", "timah", "wolfram",
            "minyak", "CPO", "sawit", "komoditas",
            "suku bunga", "inflasi", "rupiah", "dolar", "fed rate",
            "transisi energi", "EV", "carbon neutral", "ESG",
            "IHSG", "BI rate", "OJK", "pemerintah", "regulasi",
        ],
    },
    {
        "id": "analyst_recommendation",
        "label": "Rekomendasi Analis",
        "color": "#fbbf24",              # yellow
        "weight": 1.0,
        "keywords": [
            "rekomendasi", "target harga", "target price",
            "beli", "jual", "tahan", "hold", "buy", "sell",
            "potensi naik", "potensi turun", "upgrade", "downgrade",
            "analis", "sekuritas", "riset",
        ],
    },
    {
        "id": "market_noise",
        "label": "Pergerakan Pasar",
        "color": "#6b7280",              # gray
        "weight": 0.7,
        "keywords": [
            "IHSG naik", "IHSG turun", "pergerakan harian",
            "top gainers", "top losers", "saham paling aktif",
            "market wrap", "closing", "pembukaan pasar",
        ],
    },
]


REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
}


# ---------------------------------------------------------------------------
# Feed builders
# ---------------------------------------------------------------------------

def build_google_news_feeds(stock_code: str, company_names: list[str]) -> list[tuple[str, str]]:
    base = "https://news.google.com/rss/search?hl=id&gl=ID&ceid=ID:id&q="
    feeds = []
    c  = company_names[0] if company_names else stock_code
    c2 = company_names[1] if len(company_names) > 1 else ""

    feeds.append((f"GNews: {stock_code} saham",
                  base + f"{stock_code}+saham+Indonesia"))
    feeds.append((f"GNews: {c} BEI",
                  base + f"{c.replace(' ', '+')}+BEI+saham"))
    feeds.append((f"GNews: {stock_code} kinerja",
                  base + f"{stock_code}+kinerja+laporan+keuangan"))
    feeds.append((f"GNews: {c} investasi",
                  base + f"{c.replace(' ', '+')}+investasi+rekomendasi"))
    # Query 5: corporate actions
    feeds.append((f"GNews: {stock_code} akuisisi",
                  base + f"{stock_code}+akuisisi+obligasi+rights+issue"))
    if c2:
        feeds.append((f"GNews: {c2}",
                      base + f"{c2.replace(' ', '+')}+saham+IDX"))

    return feeds


def build_general_feeds() -> list[tuple[str, str]]:
    return [
        ("CNBC Indonesia Market", "https://www.cnbcindonesia.com/market/rss"),
        ("Detik Finance",         "https://finance.detik.com/rss"),
    ]


def get_company_keywords(stock_code: str) -> list[str]:
    code = stock_code.upper().strip()
    keywords = [code]
    if code in STOCK_COMPANY_MAP:
        keywords.extend(STOCK_COMPANY_MAP[code])
    return keywords


def clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_date(entry) -> tuple[str, Optional[datetime]]:
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.strftime("%d %b %Y, %H:%M"), dt
    except Exception:
        pass
    return "Tanggal tidak diketahui", None


def detect_category(title: str, summary: str) -> dict:
    """
    Deteksi kategori artikel berdasarkan kontennya.
    Prioritas: Corporate Action > Fundamental > Ownership > Sektor > Rekomendasi > Noise.
    Return dict {id, label, color, weight}.
    """
    text = (title + " " + summary).lower()
    for cat in ARTICLE_CATEGORIES:
        if any(kw.lower() in text for kw in cat["keywords"]):
            return cat
    # Default: tidak ada kategori yang cocok
    return {
        "id": "general",
        "label": "Umum",
        "color": "#6b7280",
        "weight": 0.9,
    }


def score_article(title: str, summary: str, keywords: list[str]) -> tuple[float, dict]:
    """
    Hitung relevance score sebuah artikel (0.0 – 2.0, setelah dikalikan bobot kategori).
    Returns (score, category_dict).
    """
    text_full   = (title + " " + summary).lower()
    title_lower = title.lower()
    stock_code  = keywords[0]

    # 1. Keyword match di judul
    kw_in_title   = sum(1 for kw in keywords if kw.lower() in title_lower)
    kw_score_title = min(1.0, kw_in_title / max(1, len(keywords))) * 0.4

    # 2. Keyword match di body
    kw_in_body    = sum(1 for kw in keywords if kw.lower() in text_full)
    kw_score_body = min(1.0, kw_in_body / max(1, len(keywords))) * 0.2

    # 3. Kode saham eksak di judul
    exact_bonus = 0.2 if re.search(r'\b' + re.escape(stock_code) + r'\b', title) else 0.0

    base_score = kw_score_title + kw_score_body + exact_bonus  # max ~0.8

    # 4. Deteksi kategori + terapkan weight multiplier
    category = detect_category(title, summary)
    final_score = round(base_score * category["weight"], 3)

    return final_score, category


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------

async def fetch_from_feed(
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

            combined = f"{title} {summary}"
            if not any(kw.lower() in combined.lower() for kw in keywords):
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
                "_cat_weight":    category["weight"],
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
    Fetch dan ranking artikel berita berdasarkan kategori + relevansi.
    Returns (display_articles, ai_articles).
    """
    code          = stock_code.upper().strip()
    keywords      = get_company_keywords(code)
    company_names = STOCK_COMPANY_MAP.get(code, [])

    all_articles: list[dict] = []
    seen_urls: set[str]      = set()

    google_feeds  = build_google_news_feeds(code, company_names)
    general_feeds = build_general_feeds()

    async with httpx.AsyncClient(
        timeout=15.0,
        headers=REQUEST_HEADERS,
        follow_redirects=True,
    ) as client:
        for name, url in google_feeds + general_feeds:
            articles = await fetch_from_feed(client, name, url, keywords, seen_urls)
            all_articles.extend(articles)

    # ── Sort: kategori-bobot × relevansi + recency bonus ──────────────────
    def sort_key(a: dict):
        score    = a.get("_score", 0.0)
        date_obj = a.get("_date_obj")
        if date_obj:
            now   = datetime.now(tz=timezone.utc)
            age_h = max(0, (now - date_obj).total_seconds() / 3600)
            recency_bonus = max(0.0, 0.15 * (1 - age_h / 72))
        else:
            recency_bonus = 0.0
        return score + recency_bonus

    all_articles.sort(key=sort_key, reverse=True)

    def clean(art: dict) -> dict:
        return {k: v for k, v in art.items() if not k.startswith("_")}

    display_articles = [clean(a) for a in all_articles[:max_display]]
    ai_articles      = [clean(a) for a in all_articles[:max_ai]]

    return display_articles, ai_articles
