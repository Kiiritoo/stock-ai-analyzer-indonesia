"""
services/news_hasher.py — Hash-based cache invalidation for news.

Logic:
  - Take the titles of the top 15 AI-analyzed articles
  - MD5 hash them → 32-char hex string
  - Store hash in DB alongside analysis
  - On next request: fetch fresh news → compute new hash → compare
  - If different → mark analysis stale → trigger regeneration
"""
import hashlib


def compute_news_hash(articles: list[dict], max_articles: int = 15) -> str:
    """
    Compute MD5 hash from the titles of the top N articles.
    Deterministic: same articles in same order = same hash.
    
    Args:
        articles: List of article dicts (must have 'title' key)
        max_articles: Number of top articles to include in hash
    
    Returns:
        32-character hex MD5 string, or '' if no articles
    """
    if not articles:
        return ""
    titles = [a.get("title", "") for a in articles[:max_articles] if a.get("title")]
    if not titles:
        return ""
    combined = "|".join(titles)
    return hashlib.md5(combined.encode("utf-8")).hexdigest()


def is_news_changed(old_hash: str, new_hash: str) -> bool:
    """
    Return True if news has changed (hashes differ).
    Empty hash always considered changed (first fetch).
    """
    if not old_hash or not new_hash:
        return True
    return old_hash != new_hash
