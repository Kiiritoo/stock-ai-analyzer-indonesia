"""
config.py — Environment variables & shared configuration.
All secrets are injected via HF Spaces Secrets (Settings > Variables and secrets).
"""
import os
from dotenv import load_dotenv

load_dotenv()  # For local development only. HF Spaces injects env vars directly.

# ── AI Model ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL    = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

GROQ_API_KEY    = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL      = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# Primary AI: 'groq' | 'gemini'  (groq = free 14,400 RPD; gemini = 1,500 RPD)
AI_PRIMARY      = os.environ.get("AI_PRIMARY", "groq")

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL         = os.environ.get("SUPABASE_URL", "https://rxcttmbnpqhkqzirloxh.supabase.co")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")   # Write access — KEEP SECRET
SUPABASE_ANON_KEY    = os.environ.get("SUPABASE_ANON_KEY", "")      # Read-only access (safe to expose)

# ── Cache TTL (seconds) ───────────────────────────────────────────────────────
TTL_MACRO_S       = int(os.environ.get("TTL_MACRO_S",      "7200"))   # 2 jam
TTL_NEWS_S        = int(os.environ.get("TTL_NEWS_S",       "3600"))   # 1 jam
TTL_FUNDAMENTAL_S = int(os.environ.get("TTL_FUNDAMENTAL_S","86400"))  # 24 jam
# stock_analysis: NO time-based TTL — invalidated only by news hash change

# ── API Security ──────────────────────────────────────────────────────────────
# Optional: set API_SECRET to require X-API-Key header on write endpoints
API_SECRET = os.environ.get("API_SECRET", "")

# ── App ───────────────────────────────────────────────────────────────────────
APP_ENV     = os.environ.get("APP_ENV", "production")
DEBUG_MODE  = APP_ENV == "development"
