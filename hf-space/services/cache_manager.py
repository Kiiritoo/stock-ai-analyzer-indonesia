"""
services/cache_manager.py — Supabase CRUD operations for all cached data.

Uses SUPABASE_SERVICE_KEY (bypasses RLS) for all write operations.
Public read uses ANON_KEY, but from server-side so it's always safe.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY, TTL_NEWS_S, TTL_FUNDAMENTAL_S, TTL_MACRO_S

logger = logging.getLogger(__name__)

# ── Supabase singleton client — created once at startup ───────────────────────
_supabase_client: Optional[Client] = None

def _get_client() -> Client:
    global _supabase_client
    if _supabase_client is None:
        if not SUPABASE_SERVICE_KEY:
            raise RuntimeError("SUPABASE_SERVICE_KEY not configured")
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        logger.info("Supabase client initialized (singleton)")
    return _supabase_client


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _is_expired(expires_at_str: Optional[str]) -> bool:
    """Return True if the cached row has passed its expiry time."""
    if not expires_at_str:
        return False
    try:
        dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        return _now_utc() > dt
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────
# STOCK ANALYSIS
# ─────────────────────────────────────────────────────────────

def get_cached_analysis(stock_code: str) -> Optional[dict]:
    """
    Return row from stock_analysis if it exists.
    Does NOT check staleness — caller decides based on news_hash comparison.
    """
    try:
        client = _get_client()
        result = (
            client.table("stock_analysis")
            .select("*")
            .eq("stock_code", stock_code.upper())
            .limit(1)
            .execute()
        )
        rows = result.data
        if rows:
            return rows[0]
        return None
    except Exception as e:
        logger.error(f"get_cached_analysis({stock_code}): {e}")
        return None


def save_analysis(
    stock_code: str,
    company_name: str,
    analysis: dict,
    news_hash: str,
    article_count: int,
) -> bool:
    """Upsert full analysis result into stock_analysis table."""
    try:
        client  = _get_client()
        rec     = analysis.get("recommendation", "TAHAN")
        score   = analysis.get("investment_timing", {}).get("score", 50)
        summary = analysis.get("summary", "")

        row = {
            "stock_code":       stock_code.upper(),
            "company_name":     company_name,
            "analysis":         analysis,
            "recommendation":   rec,
            "investment_score": int(score),
            "summary":          summary,
            "news_hash":        news_hash,
            "article_count":    article_count,
            "is_stale":         False,
            "generated_at":     _now_utc().isoformat(),
            "expires_at":       None,   # Never expires by time — only by news hash
        }
        client.table("stock_analysis").upsert(row, on_conflict="stock_code").execute()
        logger.info(f"Saved analysis for {stock_code} (hash={news_hash[:8]})")
        return True
    except Exception as e:
        logger.error(f"save_analysis({stock_code}): {e}")
        return False


def mark_analysis_stale(stock_code: str) -> None:
    """Flag an analysis as stale so next request triggers refresh."""
    try:
        client = _get_client()
        client.table("stock_analysis").update({"is_stale": True}).eq(
            "stock_code", stock_code.upper()
        ).execute()
    except Exception as e:
        logger.error(f"mark_analysis_stale({stock_code}): {e}")


# ─────────────────────────────────────────────────────────────
# NEWS CACHE
# ─────────────────────────────────────────────────────────────

def get_cached_news(stock_code: str) -> Optional[dict]:
    """Return cached news if not expired."""
    try:
        client = _get_client()
        result = (
            client.table("news_cache")
            .select("*")
            .eq("stock_code", stock_code.upper())
            .limit(1)
            .execute()
        )
        rows = result.data
        if rows and not _is_expired(rows[0].get("expires_at")):
            return rows[0]
        return None
    except Exception as e:
        logger.error(f"get_cached_news({stock_code}): {e}")
        return None


def save_news(
    stock_code: str,
    articles: list,
    ai_articles: list,
    news_hash: str,
) -> bool:
    """Upsert news cache."""
    try:
        client   = _get_client()
        expires  = (_now_utc() + timedelta(seconds=TTL_NEWS_S)).isoformat()
        row = {
            "stock_code":    stock_code.upper(),
            "articles":      articles,
            "ai_articles":   ai_articles,
            "news_hash":     news_hash,
            "article_count": len(articles),
            "fetched_at":    _now_utc().isoformat(),
            "expires_at":    expires,
        }
        client.table("news_cache").upsert(row, on_conflict="stock_code").execute()
        return True
    except Exception as e:
        logger.error(f"save_news({stock_code}): {e}")
        return False


# ─────────────────────────────────────────────────────────────
# FUNDAMENTAL DATA
# ─────────────────────────────────────────────────────────────

def get_cached_fundamental(stock_code: str) -> Optional[dict]:
    """Return cached fundamental data if not expired (TTL 24h)."""
    try:
        client = _get_client()
        result = (
            client.table("fundamental_data")
            .select("*")
            .eq("stock_code", stock_code.upper())
            .limit(1)
            .execute()
        )
        rows = result.data
        if rows and not _is_expired(rows[0].get("expires_at")):
            return rows[0]["data"]
        return None
    except Exception as e:
        logger.error(f"get_cached_fundamental({stock_code}): {e}")
        return None


def save_fundamental(stock_code: str, data: dict) -> bool:
    """Upsert fundamental data."""
    try:
        client  = _get_client()
        expires = (_now_utc() + timedelta(seconds=TTL_FUNDAMENTAL_S)).isoformat()
        row = {
            "stock_code": stock_code.upper(),
            "data":       data,
            "fetched_at": _now_utc().isoformat(),
            "expires_at": expires,
        }
        client.table("fundamental_data").upsert(row, on_conflict="stock_code").execute()
        return True
    except Exception as e:
        logger.error(f"save_fundamental({stock_code}): {e}")
        return False


# ─────────────────────────────────────────────────────────────
# MACRO SNAPSHOT
# ─────────────────────────────────────────────────────────────

def get_cached_macro() -> Optional[dict]:
    """Return latest macro snapshot if not expired (TTL 2h)."""
    try:
        client = _get_client()
        result = (
            client.table("macro_snapshot")
            .select("*")
            .order("fetched_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data
        if rows and not _is_expired(rows[0].get("expires_at")):
            return rows[0]["data"]
        return None
    except Exception as e:
        logger.error(f"get_cached_macro: {e}")
        return None


def save_macro(data: dict) -> bool:
    """Insert new macro snapshot (keeps history, no upsert)."""
    try:
        client  = _get_client()
        expires = (_now_utc() + timedelta(seconds=TTL_MACRO_S)).isoformat()
        row = {
            "data":       data,
            "fetched_at": _now_utc().isoformat(),
            "expires_at": expires,
        }
        client.table("macro_snapshot").insert(row).execute()
        return True
    except Exception as e:
        logger.error(f"save_macro: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# ANALYSIS REQUESTS (logging)
# ─────────────────────────────────────────────────────────────

def log_request(
    stock_code: str,
    ip_address: str,
    user_agent: str,
    from_cache: bool,
    cache_hit_type: str,
    processing_time_ms: int,
    triggered_refresh: bool,
) -> None:
    """Fire-and-forget request log."""
    try:
        client = _get_client()
        client.table("analysis_requests").insert({
            "stock_code":          stock_code.upper(),
            "ip_address":          ip_address[:64] if ip_address else None,
            "user_agent":          user_agent[:200] if user_agent else None,
            "from_cache":          from_cache,
            "cache_hit_type":      cache_hit_type,
            "processing_time_ms":  processing_time_ms,
            "triggered_refresh":   triggered_refresh,
            "requested_at":        _now_utc().isoformat(),
        }).execute()
    except Exception as e:
        logger.warning(f"log_request failed (non-critical): {e}")
