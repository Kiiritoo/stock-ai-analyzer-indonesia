import feedparser
import httpx
import re
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Daftar kode saham IDX → nama-nama perusahaan yang sering disebut di berita
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
    "BRIS": ["BRIsyariah", "Bank Syariah Indonesia", "BSI"],
    "AGRO": ["Bank Raya", "BRI Agro"],
    "MIKA": ["Mitra Keluarga", "RS Mitra Keluarga"],
    "HEAL": ["Medikaloka Hermina", "RS Hermina"],
    "SRTG": ["Saratoga Investama", "Saratoga"],
    "MYOR": ["Mayora Indah", "Mayora"],
    "ULTJ": ["Ultra Jaya", "Ultrajaya"],
    "DNET": ["Indomaret", "Indoritel Makmur"],
    "RANC": ["Ranch Market"],
}

# Headers agar tidak diblokir sebagai bot
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}


def build_google_news_urls(stock_code: str, company_names: list[str]) -> list[tuple[str, str]]:
    """
    Buat URL Google News RSS untuk kode saham + nama perusahaan.
    Google News RSS adalah sumber paling reliable — GRATIS, tidak perlu API key.
    Format: https://news.google.com/rss/search?q={query}&hl=id&gl=ID&ceid=ID:id
    """
    feeds = []
    base = "https://news.google.com/rss/search?hl=id&gl=ID&ceid=ID:id&q="

    # Query berdasarkan kode saham
    q1 = f"{stock_code}+saham+Indonesia"
    feeds.append((f"Google News ({stock_code})", base + q1.replace(" ", "+")))

    # Query berdasarkan nama perusahaan pertama
    if company_names:
        q2 = f"{company_names[0]}+saham+BEI"
        feeds.append((f"Google News ({company_names[0]})", base + q2.replace(" ", "+")))

    return feeds


def build_general_feeds() -> list[tuple[str, str]]:
    """
    Feed berita keuangan umum Indonesia yang sudah terverifikasi aktif.
    """
    return [
        ("CNBC Indonesia Market",  "https://www.cnbcindonesia.com/market/rss"),
        ("Detik Finance",          "https://finance.detik.com/rss"),
    ]


def get_company_keywords(stock_code: str) -> list[str]:
    """Kembalikan daftar kata kunci pencarian untuk suatu kode saham."""
    code = stock_code.upper().strip()
    keywords = [code]
    if code in STOCK_COMPANY_MAP:
        keywords.extend(STOCK_COMPANY_MAP[code])
    return keywords


def article_matches(text: str, keywords: list[str]) -> bool:
    """Cek apakah teks artikel mengandung salah satu keyword."""
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            return True
    return False


def clean_html(text: str) -> str:
    """Hapus HTML tags dan bersihkan whitespace."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_date(entry) -> str:
    """Ekstrak dan format tanggal dari RSS entry."""
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6])
            return dt.strftime("%d %b %Y, %H:%M")
    except Exception:
        pass
    return "Tanggal tidak diketahui"


async def fetch_from_feed(
    client: httpx.AsyncClient,
    source_name: str,
    feed_url: str,
    keywords: list[str],
    seen_urls: set,
    limit: int = 20,
) -> list[dict]:
    """Ambil artikel dari satu RSS feed dan filter berdasarkan keywords."""
    try:
        resp = await client.get(feed_url)
        if resp.status_code != 200:
            return []

        feed = feedparser.parse(resp.text)
        results = []

        for entry in feed.entries[:50]:  # Scan max 50 per feed
            title   = entry.get("title", "").strip()
            summary = clean_html(entry.get("summary", entry.get("description", "")))
            url     = entry.get("link", "")

            if not title or not url:
                continue
            if url in seen_urls:
                continue

            combined = f"{title} {summary}"
            if article_matches(combined, keywords):
                seen_urls.add(url)
                results.append({
                    "title":     title,
                    "summary":   summary[:600],
                    "url":       url,
                    "source":    source_name,
                    "published": parse_date(entry),
                })

            if len(results) >= limit:
                break

        return results

    except Exception:
        return []


async def fetch_articles(stock_code: str, max_articles: int = 15) -> list[dict]:
    """
    Ambil artikel berita dari multiple sources yang relevan dengan kode saham.

    Strategi:
    1. Google News RSS (query-based, langsung relevan) — sumber utama
    2. CNBC Indonesia + Detik Finance (feed general, filter by keyword) — sumber tambahan
    """
    code      = stock_code.upper().strip()
    keywords  = get_company_keywords(code)
    company_names = STOCK_COMPANY_MAP.get(code, [])

    found:     list[dict] = []
    seen_urls: set[str]   = set()

    # Gabungkan semua feed: Google News (specific) + feed umum
    google_feeds  = build_google_news_urls(code, company_names)
    general_feeds = build_general_feeds()

    async with httpx.AsyncClient(
        timeout=15.0,
        headers=REQUEST_HEADERS,
        follow_redirects=True,
    ) as client:

        # --- Prioritas 1: Google News (paling relevan) ---
        for name, url in google_feeds:
            if len(found) >= max_articles:
                break
            articles = await fetch_from_feed(client, name, url, keywords, seen_urls)
            found.extend(articles)

        # --- Prioritas 2: Feed umum (filter manual) ---
        for name, url in general_feeds:
            if len(found) >= max_articles:
                break
            articles = await fetch_from_feed(client, name, url, keywords, seen_urls)
            found.extend(articles)

    # Batasi total
    return found[:max_articles]
