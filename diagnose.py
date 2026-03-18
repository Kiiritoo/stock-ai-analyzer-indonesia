"""
Diagnostic script — jalankan dengan: python diagnose.py
"""
import asyncio
import httpx
import feedparser
import sys

# Fix Windows encoding
sys.stdout.reconfigure(encoding='utf-8')

RSS_FEEDS = [
    ("Bisnis Ekonomi",    "https://ekonomi.bisnis.com/feed"),
    ("Bisnis Market",     "https://market.bisnis.com/feed"),
    ("Bisnis Finansial",  "https://finansial.bisnis.com/feed"),
    ("CNBC IDX",          "https://www.cnbcindonesia.com/api/channel/rss/tag/idx"),
    ("CNBC Market",       "https://www.cnbcindonesia.com/market/rss"),
    ("Kontan Investasi",  "https://investasi.kontan.co.id/rss/berita"),
    ("Kontan Keuangan",   "https://keuangan.kontan.co.id/rss/berita"),
    ("Detik Finance",     "https://finance.detik.com/rss"),
    ("IDX Channel",       "https://www.idxchannel.com/feed"),
    ("Liputan6 Bisnis",   "https://www.liputan6.com/bisnis/feed"),
    # Google News RSS -- paling reliable
    ("Google News BBCA",  "https://news.google.com/rss/search?q=BBCA+saham+Indonesia&hl=id&gl=ID&ceid=ID:id"),
    ("Google News BCA",   "https://news.google.com/rss/search?q=Bank+Central+Asia+BEI&hl=id&gl=ID&ceid=ID:id"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

async def test_feeds():
    print("=" * 70)
    print("TEST RSS FEEDS")
    print("=" * 70)
    working = []
    async with httpx.AsyncClient(timeout=12.0, headers=HEADERS, follow_redirects=True) as client:
        for name, url in RSS_FEEDS:
            try:
                resp = await client.get(url)
                feed = feedparser.parse(resp.text)
                n = len(feed.entries)
                status = "OK" if n > 0 else "EMPTY"
                print(f"[{status:5s}] {n:3d} artikel | {name:20s} | HTTP {resp.status_code}")
                if n > 0:
                    working.append((name, url, n))
            except Exception as e:
                print(f"[FAIL ] {'':3s}          | {name:20s} | {str(e)[:50]}")
    
    print(f"\nTotal feed yang berfungsi: {len(working)}/{len(RSS_FEEDS)}")
    if working:
        print("\nContoh artikel dari Google News (jika ada):")
        for name, url, n in working:
            if "Google" in name:
                try:
                    async with httpx.AsyncClient(timeout=12.0, headers=HEADERS, follow_redirects=True) as c:
                        r = await c.get(url)
                        feed = feedparser.parse(r.text)
                        for e in feed.entries[:3]:
                            print(f"  - {e.get('title', 'No title')[:70]}")
                except:
                    pass
    return working

async def test_ollama():
    print("\n" + "=" * 70)
    print("TEST OLLAMA")
    print("=" * 70)
    prompt = """/no_think
Balas HANYA dengan JSON ini (tanpa teks lain):
{"test": "ok", "model": "qwen3:4b", "status": "running"}
"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "qwen3:4b",
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 100},
                },
            )
            data = resp.json()
            raw = data.get("response", "")
            print(f"[OK  ] Ollama berjalan.")
            print(f"Response mentah:\n{raw[:500]}")
    except Exception as e:
        print(f"[FAIL] Ollama ERROR: {e}")

async def main():
    working = await test_feeds()
    await test_ollama()

asyncio.run(main())
