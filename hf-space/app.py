"""
app.py — IDX Stock Analyzer API (HF Spaces FastAPI)

v2 Improvements:
  - Thundering herd prevention via per-stock async locks
  - Singleton Supabase client (no reconnect per request)
  - Process-level in-memory macro cache (5 min)
  - IP-based rate limiting (20 analyze calls/hour per IP)
  - Stale cache fallback — serve old result when both AIs are down
  - Smart news hash — only hash high-weight articles
  - /ping — ultra-lightweight keepalive for cron-job.org
  - /api/warmup — smart background news refresh + stale detection
  - /api/stats — observability dashboard
"""
import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
from services import news_fetcher, macro_fetcher, fundamental_fetcher, analyzer, cache_manager
from services.news_hasher import compute_news_hash, is_news_changed
from services.news_fetcher import STOCK_COMPANY_MAP

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app")

# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="IDX Stock Analyzer API",
    version="2.0.0",
    description="AI-powered Indonesia stock analysis — Groq+Gemini+Supabase cache",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup metrics ────────────────────────────────────────────────────────────
_startup_time   = time.monotonic()
_ping_count     = 0
_analyze_count  = 0
_cache_hit_count = 0
_warmup_count   = 0

@app.on_event("startup")
async def _startup():
    """Pre-warm macro cache on startup so first user request is faster."""
    logger.info("Startup: pre-loading macro cache...")
    try:
        macro = await _get_macro_with_cache()
        _set_process_macro(macro)
        logger.info("Startup: macro cache ready")
    except Exception as e:
        logger.warning(f"Startup: macro pre-load failed (non-fatal): {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# THUNDERING HERD PREVENTION
# Per-stock async lock — only 1 AI generation runs per stock at a time.
# All other concurrent requests for the same stock wait, then read from cache.
# ═══════════════════════════════════════════════════════════════════════════════
_stock_locks: dict[str, asyncio.Lock] = {}
_locks_meta:  dict[str, int]          = {}   # track waiters

def _get_stock_lock(stock_code: str) -> asyncio.Lock:
    if stock_code not in _stock_locks:
        _stock_locks[stock_code] = asyncio.Lock()
    return _stock_locks[stock_code]


# ═══════════════════════════════════════════════════════════════════════════════
# PROCESS-LEVEL IN-MEMORY CACHE (Macro only — shared across all stocks)
# Avoids Supabase round-trip for every /api/analyze call.
# ═══════════════════════════════════════════════════════════════════════════════
_process_macro_cache: Optional[dict] = None
_process_macro_time:  float           = 0.0
_MACRO_PROCESS_TTL    = 300   # 5 minutes


def _get_process_macro() -> Optional[dict]:
    if _process_macro_cache and (time.monotonic() - _process_macro_time) < _MACRO_PROCESS_TTL:
        return _process_macro_cache
    return None


def _set_process_macro(data: dict) -> None:
    global _process_macro_cache, _process_macro_time
    _process_macro_cache = data
    _process_macro_time  = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════════
# IP RATE LIMITING (simple in-memory — no Redis needed)
# 10 analyze requests per IP per hour.
# ═══════════════════════════════════════════════════════════════════════════════
_ip_counts: dict[str, list[float]] = defaultdict(list)   # ip → list of request timestamps
_RATE_LIMIT_WINDOW = 3600    # 1 hour
_RATE_LIMIT_MAX    = 20      # max requests per hour per IP


def _check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed, False if rate limited."""
    if not ip or ip in ("127.0.0.1", "::1", "unknown"):
        return True   # Skip localhost
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW
    # Prune old timestamps
    _ip_counts[ip] = [t for t in _ip_counts[ip] if t > window_start]
    if len(_ip_counts[ip]) >= _RATE_LIMIT_MAX:
        return False
    _ip_counts[ip].append(now)
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# SMART NEWS HASH — Only hash high-weight articles (weight >= 1.2)
# Prevents AI regeneration for minor noise articles.
# High-weight: corporate_action (1.8), fundamental (1.6), ownership (1.5), sector_macro (1.2)
# Ignored: analyst_recommendation (1.0), market_noise (0.7), general (0.9)
# ═══════════════════════════════════════════════════════════════════════════════
_HIGH_WEIGHT_CATEGORIES = {"corporate_action", "fundamental", "ownership", "sector_macro"}

def _compute_smart_hash(articles: list[dict]) -> str:
    """Hash only high-signal articles to avoid noise-triggered regeneration."""
    high_signal = [
        a for a in articles[:15]
        if a.get("category") in _HIGH_WEIGHT_CATEGORIES
    ]
    # Fallback to all articles if no high-signal ones found
    target = high_signal if len(high_signal) >= 2 else articles[:15]
    return compute_news_hash(target)


# ── Request model ─────────────────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    stock_code:    str
    force_refresh: bool = False


# ── Price fetcher ─────────────────────────────────────────────────────────────
def _fetch_price_sync(ticker_symbol: str) -> dict:
    try:
        hist = yf.Ticker(ticker_symbol).history(period="1y", auto_adjust=True)
        if hist.empty:
            return {"available": False}
        closes     = hist["Close"]
        now        = float(closes.iloc[-1])
        last_date  = hist.index[-1].strftime("%d %b %Y")
        def _ago(n):
            return float(closes.iloc[-n]) if len(closes) >= n else None
        p1m = _ago(21); p6m = _ago(126); p1y = _ago(252)
        def _pct(p): return round((now - p) / p * 100, 2) if p else None
        return {
            "available":     True,
            "current_price": round(now, 2),
            "last_date":     last_date,
            "price_1m_ago":  round(p1m, 2) if p1m else None,
            "price_6m_ago":  round(p6m, 2) if p6m else None,
            "price_1y_ago":  round(p1y, 2) if p1y else None,
            "change_1m_pct": _pct(p1m),
            "change_6m_pct": _pct(p6m),
            "change_1y_pct": _pct(p1y),
        }
    except Exception as e:
        return {"available": False, "error": str(e)[:80]}


async def _fetch_price(ticker_symbol: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_price_sync, ticker_symbol)


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# GET /ping — Ultra-lightweight keepalive (no DB, no computation, <1ms)
# Use this from cron-job.org every 5 minutes to keep HF Space awake.
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/ping")
def ping():
    global _ping_count
    _ping_count += 1
    uptime_s = int(time.monotonic() - _startup_time)
    return {
        "pong":    True,
        "uptime":  f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m {uptime_s % 60}s",
        "pings":   _ping_count,
        "time":    datetime.now(tz=timezone.utc).isoformat(),
    }


@app.get("/")
@app.get("/health")
def health():
    return {
        "status":  "ok",
        "service": "IDX Stock Analyzer API",
        "version": "2.0.0",
        "time":    datetime.now(tz=timezone.utc).isoformat(),
        "model":   f"{config.GROQ_MODEL} (primary) / {config.GEMINI_MODEL} (fallback)",
        "env":     config.APP_ENV,
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/analyze — Main endpoint with all optimizations
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/analyze")
async def analyze_stock(req: AnalyzeRequest, request: Request):
    """
    Cache-first analysis with thundering herd prevention.

    Flow:
      1. Rate limit check (20 req/hour per IP)
      2. Fetch news (Supabase cache 1h, smart hash)
      3. Acquire per-stock lock (prevents duplicate AI calls)
      4. Check analysis cache:
         - Hash same → release lock, return cache instantly
         - Hash different → run AI (only 1 concurrent per stock)
      5. Save to Supabase → release lock
      6. Other waiting requests → read from cache (step 4)
    """
    t_start    = time.monotonic()
    stock_code = req.stock_code.upper().strip()
    ticker     = f"{stock_code}.JK"
    ip         = request.client.host if request.client else "unknown"
    ua         = request.headers.get("user-agent", "")[:200]
    company_names = STOCK_COMPANY_MAP.get(stock_code, [])
    company_name  = company_names[0] if company_names else stock_code

    # ── Rate limit ────────────────────────────────────────────────────────────
    if not req.force_refresh and not _check_rate_limit(ip):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {_RATE_LIMIT_MAX} requests/hour per IP. "
                   "Try again later."
        )

    logger.info(f"[{stock_code}] Request | ip={ip} | force={req.force_refresh}")

    # ── Step 1: Fetch news (Supabase cache first) ─────────────────────────────
    cached_news = cache_manager.get_cached_news(stock_code) if not req.force_refresh else None
    if cached_news:
        logger.info(f"[{stock_code}] News: Supabase cache hit")
        display_articles = cached_news.get("articles",    [])
        ai_articles      = cached_news.get("ai_articles", [])
    else:
        logger.info(f"[{stock_code}] News: fetching live RSS...")
        display_articles, ai_articles = await news_fetcher.fetch_articles(stock_code)
        cache_manager.save_news(stock_code, display_articles, ai_articles,
                                compute_news_hash(ai_articles))
        logger.info(f"[{stock_code}] News: {len(display_articles)} articles fetched")

    # Smart hash (ignore noise articles)
    new_hash = _compute_smart_hash(ai_articles)

    # ── Step 2: Acquire per-stock lock (thundering herd prevention) ───────────
    lock = _get_stock_lock(stock_code)

    async with lock:
        # Re-check cache AFTER acquiring lock — another request may have just generated
        cached_row = cache_manager.get_cached_analysis(stock_code) if not req.force_refresh else None
        cached_hash = (cached_row or {}).get("news_hash", "")
        hash_changed = is_news_changed(cached_hash, new_hash)

        if cached_row and not hash_changed:
            # ✅ Cache HIT (either first request or a waiter that got lucky)
            logger.info(f"[{stock_code}] Analysis: cache HIT (hash unchanged)")
            ms = int((time.monotonic() - t_start) * 1000)
            cache_manager.log_request(stock_code, ip, ua, True, "full_cache", ms, False)
            return JSONResponse({
                "stock_code":      stock_code,
                "ticker":          ticker,
                "company_name":    company_name,
                "from_cache":      True,
                "cache_hit_type":  "full_cache",
                "news_changed":    False,
                "generated_at":    cached_row.get("generated_at"),
                "analysis":        cached_row.get("analysis", {}),
                "articles":        display_articles,
                "article_count":   len(display_articles),
                "ai_article_count":len(ai_articles),
                "fundamental":     None,
                "processing_ms":   ms,
            })

        # ── Step 3: Cache MISS — run AI pipeline ──────────────────────────────
        logger.info(f"[{stock_code}] Analysis: cache {'STALE' if cached_row else 'MISS'} — running AI")

        # Fetch macro (process cache → Supabase cache → live fetch)
        macro_data = _get_process_macro()
        if macro_data is None:
            macro_data = await _get_macro_with_cache()
            _set_process_macro(macro_data)

        # Fetch fundamental + price in parallel
        fundamental_data, price_data = await asyncio.gather(
            _get_fundamental_with_cache(stock_code),
            _fetch_price(ticker),
        )

        macro_ctx = macro_fetcher.build_macro_context(macro_data, stock_code)

        # ── Step 4: Run AI (Groq → Gemini fallback) ───────────────────────────
        logger.info(f"[{stock_code}] Calling AI ({config.AI_PRIMARY})...")
        ai_error = None
        try:
            analysis = await analyzer.analyze_with_gemini(
                stock_code=stock_code,
                company_names=company_names,
                articles=ai_articles,
                price_data=price_data,
                macro_context=macro_ctx,
                fundamental_data=fundamental_data,
            )
        except Exception as e:
            ai_error = str(e)
            logger.error(f"[{stock_code}] AI failed: {ai_error}")
            # ── Stale cache fallback — serve old result rather than 503 ───────
            if cached_row:
                logger.warning(f"[{stock_code}] Serving STALE cache as fallback")
                ms = int((time.monotonic() - t_start) * 1000)
                stale_analysis = cached_row.get("analysis", {})
                stale_analysis["_stale_warning"] = (
                    f"AI unavailable ({ai_error[:80]}). "
                    "Serving last cached result."
                )
                cache_manager.log_request(stock_code, ip, ua, True, "stale", ms, False)
                return JSONResponse({
                    "stock_code":     stock_code,
                    "ticker":         ticker,
                    "company_name":   company_name,
                    "from_cache":     True,
                    "cache_hit_type": "stale_fallback",
                    "news_changed":   True,
                    "is_stale":       True,
                    "generated_at":   cached_row.get("generated_at"),
                    "analysis":       stale_analysis,
                    "articles":       display_articles,
                    "article_count":  len(display_articles),
                    "processing_ms":  ms,
                }, status_code=200)
            raise HTTPException(status_code=503, detail=f"AI analysis failed: {ai_error}")

        # ── Step 5: Save to Supabase (lock still held — safe to write) ────────
        cache_manager.save_analysis(
            stock_code=stock_code,
            company_name=company_name,
            analysis=analysis,
            news_hash=new_hash,
            article_count=len(display_articles),
        )

    # Lock released — other waiting requests will now hit cache

    ms = int((time.monotonic() - t_start) * 1000)
    logger.info(f"[{stock_code}] Done in {ms}ms | provider={analysis.get('ai_provider')}")
    cache_manager.log_request(stock_code, ip, ua, False, "miss", ms, True)

    return JSONResponse({
        "stock_code":       stock_code,
        "ticker":           ticker,
        "company_name":     company_name,
        "from_cache":       False,
        "cache_hit_type":   "miss",
        "news_changed":     hash_changed,
        "generated_at":     datetime.now(tz=timezone.utc).isoformat(),
        "analysis":         analysis,
        "articles":         display_articles,
        "article_count":    len(display_articles),
        "ai_article_count": len(ai_articles),
        "fundamental":      fundamental_data,
        "processing_ms":    ms,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/analysis/{stock_code} — Read-only from Supabase
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/analysis/{stock_code}")
async def get_analysis(stock_code: str):
    row = cache_manager.get_cached_analysis(stock_code.upper())
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No analysis for {stock_code.upper()}. Call POST /api/analyze first."
        )
    return JSONResponse({
        "stock_code":   row["stock_code"],
        "company_name": row.get("company_name"),
        "from_cache":   True,
        "is_stale":     row.get("is_stale", False),
        "generated_at": row.get("generated_at"),
        "analysis":     row.get("analysis", {}),
        "news_hash":    row.get("news_hash"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/macro
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/macro")
async def get_macro():
    macro = _get_process_macro() or await _get_macro_with_cache()
    _set_process_macro(macro)
    return JSONResponse(macro)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/fundamental/{stock_code}
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/fundamental/{stock_code}")
async def get_fundamental(stock_code: str):
    return JSONResponse(await _get_fundamental_with_cache(stock_code.upper()))


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/status/{stock_code} — Debug cache status
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# GET /api/stats — Observability dashboard
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
def get_stats():
    """Runtime stats — useful for monitoring and debugging."""
    uptime_s = int(time.monotonic() - _startup_time)
    return JSONResponse({
        "uptime":         f"{uptime_s // 3600}h {(uptime_s % 3600) // 60}m {uptime_s % 60}s",
        "uptime_seconds": uptime_s,
        "since":          datetime.now(tz=timezone.utc).isoformat(),
        "counts": {
            "ping":         _ping_count,
            "analyze":      _analyze_count,
            "cache_hits":   _cache_hit_count,
            "warmup_runs":  _warmup_count,
        },
        "cache_hit_rate": f"{(_cache_hit_count / _analyze_count * 100):.1f}%" if _analyze_count else "N/A",
        "active_locks":   {k: v.locked() for k, v in _stock_locks.items()},
        "process_macro":  _process_macro_cache is not None,
        "ai_primary":     config.AI_PRIMARY,
        "groq_model":     config.GROQ_MODEL,
        "gemini_model":   config.GEMINI_MODEL,
        "rate_limit":     f"{_RATE_LIMIT_MAX} req/{_RATE_LIMIT_WINDOW//3600}h per IP",
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/warmup — Smart background cache refresh
# Cron: every 30 minutes — checks top stocks for stale news, marks stale.
# Optional: ?regen=true runs AI pre-generation in background.
# ─────────────────────────────────────────────────────────────────────────────

# Top 30 IDX stocks by market cap / popularity
_TOP_STOCKS = [
    "BBCA","BBRI","BYAN","TPIA","BMRI","TLKM","ASII","GOTO","UNVR","ICBP",
    "INDF","KLBF","BBNI","TOWR","PGAS","ANTM","MDKA","CUAN","AMRT","BRIS",
    "MTEL","SMGR","AALI","DSNG","INCO","MEDC","PTBA","ADRO","SIDO","BSDE",
]

async def _warmup_stock(stock_code: str, regen: bool) -> dict:
    """Check one stock: refresh news, compare hash, optionally pre-gen AI."""
    try:
        # Always fetch fresh news (bypasses 1h Supabase cache for warmup)
        display_articles, ai_articles = await news_fetcher.fetch_articles(stock_code)
        new_hash = _compute_smart_hash(ai_articles)

        # Save fresh news to Supabase
        cache_manager.save_news(stock_code, display_articles, ai_articles,
                                compute_news_hash(ai_articles))

        # Compare with cached analysis hash
        analysis_row = cache_manager.get_cached_analysis(stock_code)
        if not analysis_row:
            status = "no_cache"
        else:
            old_hash = analysis_row.get("news_hash", "")
            if is_news_changed(old_hash, new_hash):
                # Mark as stale in DB — next user request will re-generate
                cache_manager.mark_analysis_stale(stock_code)
                status = "marked_stale"
                if regen:
                    # Pre-generate AI in background
                    try:
                        macro_data = _get_process_macro() or await _get_macro_with_cache()
                        fundamental_data, price_data = await asyncio.gather(
                            _get_fundamental_with_cache(stock_code),
                            _fetch_price(f"{stock_code}.JK"),
                        )
                        company_names = STOCK_COMPANY_MAP.get(stock_code, [])
                        macro_ctx = macro_fetcher.build_macro_context(macro_data, stock_code)
                        analysis = await analyzer.analyze_with_gemini(
                            stock_code=stock_code,
                            company_names=company_names,
                            articles=ai_articles,
                            price_data=price_data,
                            macro_context=macro_ctx,
                            fundamental_data=fundamental_data,
                        )
                        company_name = company_names[0] if company_names else stock_code
                        cache_manager.save_analysis(
                            stock_code=stock_code,
                            company_name=company_name,
                            analysis=analysis,
                            news_hash=new_hash,
                            article_count=len(display_articles),
                        )
                        status = "regenerated"
                    except Exception as e:
                        status = f"regen_failed:{str(e)[:40]}"
            else:
                status = "fresh"

        return {"stock": stock_code, "status": status, "articles": len(display_articles)}
    except Exception as e:
        return {"stock": stock_code, "status": f"error:{str(e)[:50]}", "articles": 0}


async def _run_warmup(stocks: list[str], regen: bool) -> None:
    """Background task: process stocks in small batches (avoid hammering APIs)."""
    global _warmup_count
    _warmup_count += 1
    batch_size = 3  # 3 concurrent stocks at a time
    results = []
    for i in range(0, len(stocks), batch_size):
        batch   = stocks[i:i + batch_size]
        batch_r = await asyncio.gather(*[_warmup_stock(s, regen) for s in batch])
        results.extend(batch_r)
        await asyncio.sleep(2)  # Polite delay between batches
    stale_count = sum(1 for r in results if r["status"] in ("marked_stale", "regenerated"))
    logger.info(f"[warmup] Done: {len(results)} stocks, {stale_count} stale/refreshed")


@app.get("/api/warmup")
async def warmup(
    background_tasks: BackgroundTasks,
    stocks: str = Query(default="", description="Comma-separated stock codes, or empty for top 30"),
    regen:  bool = Query(default=False, description="Pre-generate AI for stale stocks"),
):
    """
    Smart cache refresh.
    - Fetches fresh news for each stock
    - Compares smart hash with cached analysis hash
    - Marks stale if news changed
    - If regen=true: also pre-generates AI (takes longer)

    Recommended cron setup:
      /api/warmup           every 30 min  (check only)
      /api/warmup?regen=true every 2 hours (pre-generate)
    """
    target = [
        s.strip().upper() for s in stocks.split(",") if s.strip()
    ] if stocks else _TOP_STOCKS

    if len(target) > 50:
        raise HTTPException(status_code=400, detail="Max 50 stocks per warmup call")

    background_tasks.add_task(_run_warmup, target, regen)

    return JSONResponse({
        "status":  "warmup_started",
        "stocks":  target,
        "count":   len(target),
        "regen":   regen,
        "message": "Running in background. Check /api/stats for progress.",
    })


@app.get("/api/status/{stock_code}")
async def get_status(stock_code: str):
    code         = stock_code.upper()
    news_row     = cache_manager.get_cached_news(code)
    analysis_row = cache_manager.get_cached_analysis(code)
    fund_data    = cache_manager.get_cached_fundamental(code)
    lock         = _stock_locks.get(code)

    return JSONResponse({
        "stock_code": code,
        "lock": {
            "exists":  lock is not None,
            "locked":  lock.locked() if lock else False,
        },
        "news_cache": {
            "cached":        news_row is not None,
            "article_count": news_row.get("article_count") if news_row else None,
            "news_hash":     (news_row.get("news_hash","") or "")[:8]+"..." if news_row else None,
            "fetched_at":    news_row.get("fetched_at") if news_row else None,
            "expires_at":    news_row.get("expires_at") if news_row else None,
        },
        "analysis_cache": {
            "cached":           analysis_row is not None,
            "recommendation":   analysis_row.get("recommendation") if analysis_row else None,
            "investment_score": analysis_row.get("investment_score") if analysis_row else None,
            "is_stale":         analysis_row.get("is_stale") if analysis_row else None,
            "news_hash":        (analysis_row.get("news_hash","") or "")[:8]+"..." if analysis_row else None,
            "generated_at":     analysis_row.get("generated_at") if analysis_row else None,
        },
        "fundamental_cache": {
            "cached": fund_data is not None,
            "sector": fund_data.get("sector_label") if fund_data else None,
            "ttm_period": fund_data.get("ttm_period") if fund_data else None,
            "pe":     (fund_data.get("valuation") or {}).get("pe_trailing") if fund_data else None,
        },
        "rate_limit": {
            "ip":          "hidden",
            "window_secs": _RATE_LIMIT_WINDOW,
            "max_per_window": _RATE_LIMIT_MAX,
        },
        "process_macro_cached": _process_macro_cache is not None,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────
async def _get_macro_with_cache() -> dict:
    cached = cache_manager.get_cached_macro()
    if cached:
        return cached
    logger.info("Macro: fetching live data...")
    data = await macro_fetcher.fetch_all_macro()
    cache_manager.save_macro(data)
    return data


async def _get_fundamental_with_cache(stock_code: str) -> dict:
    cached = cache_manager.get_cached_fundamental(stock_code)
    if cached:
        return cached
    logger.info(f"Fundamental: fetching {stock_code} from yfinance...")
    data = await fundamental_fetcher.fetch_fundamentals(stock_code)
    if data.get("available"):
        cache_manager.save_fundamental(stock_code, data)
    return data
