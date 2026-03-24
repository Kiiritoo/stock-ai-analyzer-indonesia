"""
test_api.py — Local testing script for IDX Stock Analyzer API.
Run: python test_api.py [stock_code] [base_url]

Examples:
  python test_api.py                         # Test BBCA on localhost
  python test_api.py TLKM                    # Test TLKM on localhost
  python test_api.py BBCA https://michael123333-stock-analyzer.hf.space  # Test on HF Spaces
"""
import sys
import time
import json
import httpx

BASE_URL   = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:7860"
STOCK_CODE = sys.argv[1].upper() if len(sys.argv) > 1 else "BBCA"
HF_TOKEN   = sys.argv[3] if len(sys.argv) > 3 else ""
TIMEOUT    = 120

# Auth header for private HF Spaces
HEADERS = {"Authorization": f"Bearer {HF_TOKEN}"} if HF_TOKEN else {}


def sep(title=""):
    print(f"\n{'-'*55}")
    if title:
        print(f"  {title}")
        print(f"{'-'*55}")


def ok(label, value=""):
    print(f"  [OK] {label}: {value}")

def warn(label, value=""):
    print(f"  [!!] {label}: {value}")

def err(label, value=""):
    print(f"  [XX] {label}: {value}")


def test_health():
    sep("1. Health Check")
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=15, headers=HEADERS)
        r.raise_for_status()
        data = r.json()
        ok("Status",   data.get("status"))
        ok("Model",    data.get("model"))
        ok("Version",  data.get("version"))
        ok("Env",      data.get("env"))
        return True
    except Exception as e:
        err("Health check failed", str(e))
        return False


def test_macro():
    sep("2. Macro Data")
    try:
        t = time.monotonic()
        r = httpx.get(f"{BASE_URL}/api/macro", timeout=60, headers=HEADERS)
        r.raise_for_status()
        data  = r.json()
        ms    = int((time.monotonic() - t) * 1000)
        market = data.get("market", {})
        ok("Response time", f"{ms}ms")
        ok("IHSG",     f"{market.get('IHSG', {}).get('price', 'N/A'):,.2f}" if market.get('IHSG', {}).get('available') else "unavailable")
        ok("USD/IDR",  f"{market.get('USD_IDR', {}).get('price', 'N/A'):,.0f}" if market.get('USD_IDR', {}).get('available') else "unavailable")
        ok("BI Rate",  f"{data.get('bi_rate', {}).get('rate', '?')}%")
        ok("Fed Rate", f"{data.get('fed_rate', {}).get('rate', '?')}%")
        gn = data.get("global_news", [])
        ok("Global News", f"{len(gn)} articles")
        return True
    except Exception as e:
        err("Macro test failed", str(e))
        return False


def test_fundamental(stock_code: str):
    sep(f"3. Fundamental Data — {stock_code}")
    try:
        t = time.monotonic()
        r = httpx.get(f"{BASE_URL}/api/fundamental/{stock_code}", timeout=60, headers=HEADERS)
        r.raise_for_status()
        data = r.json()
        ms   = int((time.monotonic() - t) * 1000)
        ok("Response time", f"{ms}ms")
        ok("Available",  data.get("available"))
        ok("Sector",     data.get("sector_label"))
        v = data.get("valuation", {})
        ok("P/E",        v.get("pe_trailing"))
        ok("P/B",        v.get("pb_ratio"))
        ok("Verdict",    v.get("verdict"))
        p = data.get("profitability", {})
        ok("ROE",        f"{p.get('roe_pct')}%")
        ok("Net Margin", f"{p.get('net_margin_pct')}%")
        ttm = data.get("ttm_income", {})
        if ttm:
            ok("TTM Net Income", ttm.get("Net Income"))
            ok("TTM Period",     data.get("ttm_period"))
        q = data.get("quarterly_net_income", [])
        ok("Quarterly Points", len(q))
        return True
    except Exception as e:
        err("Fundamental test failed", str(e))
        return False


def test_cache_status(stock_code: str):
    sep(f"4. Cache Status — {stock_code}")
    try:
        r = httpx.get(f"{BASE_URL}/api/status/{stock_code}", timeout=15, headers=HEADERS)
        r.raise_for_status()
        data = r.json()
        nc = data.get("news_cache", {})
        ac = data.get("analysis_cache", {})
        print(f"  News Cache:")
        print(f"    cached={nc.get('cached')} | articles={nc.get('article_count')} | hash={nc.get('news_hash')}")
        print(f"    expires={nc.get('expires_at','N/A')[:19] if nc.get('expires_at') else 'N/A'}")
        print(f"  Analysis Cache:")
        print(f"    cached={ac.get('cached')} | rec={ac.get('recommendation')} | score={ac.get('investment_score')}")
        print(f"    is_stale={ac.get('is_stale')} | hash={ac.get('news_hash')}")
        print(f"    generated={ac.get('generated_at','N/A')[:19] if ac.get('generated_at') else 'N/A'}")
        return True
    except Exception as e:
        err("Status check failed", str(e))
        return False


def test_analyze(stock_code: str):
    sep(f"5. Analyze — {stock_code} (may take 30-90s on first run)")
    try:
        print(f"  📡 Sending POST /api/analyze ({stock_code})...")
        t = time.monotonic()
        r = httpx.post(
            f"{BASE_URL}/api/analyze",
            json={"stock_code": stock_code},
            timeout=TIMEOUT,
            headers=HEADERS,
        )
        r.raise_for_status()
        data = r.json()
        ms   = int((time.monotonic() - t) * 1000)

        ok("Response time",    f"{ms}ms")
        ok("From cache",       data.get("from_cache"))
        ok("Cache hit type",   data.get("cache_hit_type"))
        ok("News changed",     data.get("news_changed"))
        ok("Articles total",   data.get("article_count"))
        ok("Articles for AI",  data.get("ai_article_count"))

        analysis = data.get("analysis", {})
        ok("Recommendation",   analysis.get("recommendation"))
        ok("Investment score", analysis.get("investment_timing", {}).get("score"))
        ok("Investment signal",analysis.get("investment_timing", {}).get("signal"))
        ok("Sentiment",        analysis.get("sentiment", {}).get("overall"))
        ok("Short-term",       analysis.get("short_term", {}).get("signal"))
        ok("Long-term",        analysis.get("long_term", {}).get("signal"))
        ok("Summary",          f"\n    → {analysis.get('summary','N/A')[:120]}...")

        fund_ai = analysis.get("fundamental", {})
        if fund_ai:
            ok("AI Valuation",    fund_ai.get("valuation_verdict"))
            ok("AI Health",       fund_ai.get("financial_health"))
            ok("AI Growth",       fund_ai.get("growth_quality"))

        kf = analysis.get("key_factors", [])
        if kf:
            print(f"  Key Factors:")
            for f in kf[:3]:
                print(f"    • {f}")
        return True
    except httpx.TimeoutException:
        err("TIMEOUT", f"API did not respond within {TIMEOUT}s. HF Space may be sleeping — try again.")
        return False
    except Exception as e:
        err("Analyze test failed", str(e))
        return False


def test_read_cache(stock_code: str):
    sep(f"6. Read-only Cache — GET /api/analysis/{stock_code}")
    try:
        r = httpx.get(f"{BASE_URL}/api/analysis/{stock_code}", timeout=15, headers=HEADERS)
        if r.status_code == 404:
            warn("Not cached yet", "Run POST /api/analyze first")
            return True
        r.raise_for_status()
        data = r.json()
        ok("From cache",    data.get("from_cache"))
        ok("Is stale",      data.get("is_stale"))
        ok("Generated",     str(data.get("generated_at",""))[:19])
        ok("Recommendation",data.get("analysis", {}).get("recommendation"))
        return True
    except Exception as e:
        err("Read cache test failed", str(e))
        return False


if __name__ == "__main__":
    print(f"\n{'═'*55}")
    print(f"  IDX Stock Analyzer API — Test Suite")
    print(f"  Base URL:   {BASE_URL}")
    print(f"  Token:      {'set (' + HF_TOKEN[:8] + '...)' if HF_TOKEN else 'not set (public/local)'}")
    print(f"{'═'*55}")

    results = []
    results.append(("Health",       test_health()))
    results.append(("Macro",        test_macro()))
    results.append(("Fundamental",  test_fundamental(STOCK_CODE)))
    results.append(("Cache Status", test_cache_status(STOCK_CODE)))
    results.append(("Analyze",      test_analyze(STOCK_CODE)))
    results.append(("Read Cache",   test_read_cache(STOCK_CODE)))

    sep("RESULTS SUMMARY")
    passed = sum(1 for _, ok in results if ok)
    for name, result in results:
        print(f"  {'✅' if result else '❌'} {name}")
    print(f"\n  {passed}/{len(results)} tests passed")
    print(f"{'═'*55}\n")
