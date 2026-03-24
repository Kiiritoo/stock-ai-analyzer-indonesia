"""
services/analyzer.py — AI analysis with Groq (primary) + Gemini (fallback).

Tier strategy:
  1. Groq  (Llama 3.3 70B) — FREE, 14,400 RPD, blazing fast
  2. Gemini (2.0 Flash)     — FREE, 1,500 RPD, best Indonesian quality
  → Total: ~15,900 AI calls/day free

With Supabase caching, real production usage << 150 calls/day.
"""
import json
import logging
import re
from typing import Optional

import google.generativeai as genai
from groq import Groq

from config import (
    GEMINI_API_KEY, GEMINI_MODEL,
    GROQ_API_KEY,   GROQ_MODEL,
    AI_PRIMARY,
)

logger = logging.getLogger(__name__)

# Configure both clients
genai.configure(api_key=GEMINI_API_KEY)
_groq_client: Optional[Groq] = None

def _get_groq() -> Optional[Groq]:
    global _groq_client
    if not GROQ_API_KEY:
        return None
    if _groq_client is None:
        _groq_client = Groq(api_key=GROQ_API_KEY)
    return _groq_client


# ── Formatting helpers ────────────────────────────────────────────────────────
def _fmt_price(p) -> str:
    if p is None: return "N/A"
    return f"Rp {int(p):,}".replace(",", ".")

def _fmt_pct(p) -> str:
    if p is None: return "N/A"
    return f"{'+' if p > 0 else ''}{p:.2f}%"


# ── Article formatter ─────────────────────────────────────────────────────────
PRIORITY_MARK = {
    "corporate_action":      "[CORPORATE ACTION ***]",
    "fundamental":           "[LAPORAN KEUANGAN ***]",
    "ownership":             "[SMART MONEY **]",
    "sector_macro":          "[SEKTOR/MAKRO *]",
    "analyst_recommendation":"[REKOMENDASI ANALIS]",
    "market_noise":          "[NOISE - bobot rendah]",
}

def _format_articles(articles: list[dict]) -> str:
    lines = []
    for i, art in enumerate(articles[:15], 1):
        mark = PRIORITY_MARK.get(art.get("category", "general"), "")
        lines.append(
            f"[{i}]{mark} {art['title']} "
            f"({art.get('source','')}, {art.get('published','')})"
        )
    return "\n".join(lines)


# ── Fundamental context builder ───────────────────────────────────────────────
def build_fundamental_context(f: dict) -> str:
    if not f or not f.get("available"):
        return ""
    v  = f.get("valuation", {})
    p  = f.get("profitability", {})
    g  = f.get("growth", {})
    h  = f.get("financial_health", {})
    d  = f.get("dividends", {})
    q  = f.get("quarterly_net_income", [])
    inc = f.get("income_statement", {})
    bench  = f.get("sector_benchmark", {})
    s_lbl  = f.get("sector_label", "Umum")
    ticker = f.get("ticker", "")

    def rp(val):
        if val is None: return "N/A"
        for thr, suf in [(1e12,"T"), (1e9,"B"), (1e6,"M")]:
            if abs(val) >= thr: return f"Rp {val/thr:.1f}{suf}"
        return f"Rp {val:,.0f}"
    def pct(val): return f"{val:+.1f}%" if val is not None else "N/A"
    def xf(val):  return f"{val:.1f}x"  if val is not None else "N/A"

    q_str = ""
    if q:
        last4 = q[-4:]
        parts = [f"{x['period'][-7:]}: {rp(x['value'])}" for x in last4]
        vals  = [x["value"] for x in last4 if x["value"] is not None]
        trend = "NAIK" if len(vals)>=2 and vals[-1]>vals[0] else ("TURUN" if len(vals)>=2 and vals[-1]<vals[0] else "STABIL")
        q_str = f"Laba Bersih Kuartalan: {' - '.join(parts)} [{trend}]"

    rev_trend = ""
    if "Total Revenue" in inc:
        rev   = inc["Total Revenue"]
        dates = sorted(rev.keys())[-3:]
        pairs = [(d, rev[d]) for d in dates if rev.get(d) is not None]
        parts = []
        for i, (dt, val) in enumerate(pairs):
            if i > 0 and pairs[i-1][1]:
                g_pct = (val - pairs[i-1][1]) / abs(pairs[i-1][1]) * 100
                parts.append(f"{dt[:4]}: {rp(val)} ({g_pct:+.0f}%)")
            else:
                parts.append(f"{dt[:4]}: {rp(val)}")
        rev_trend = f"Revenue Tahunan: {' - '.join(parts)}"

    lines = [
        f"FUNDAMENTAL ({ticker} | Sektor: {s_lbl})",
        f"Benchmark: P/E {bench.get('pe_range','?')} | P/B {bench.get('pb_range','?')} | ROE {bench.get('roe_min','?')}",
        "",
        f"VALUASI: {v.get('verdict','?')}",
        f"  P/E: {xf(v.get('pe_trailing'))} | P/E Fwd: {xf(v.get('pe_forward'))} | P/B: {xf(v.get('pb_ratio'))} | MCap: {rp(v.get('market_cap'))}",
        "",
        f"PROFITABILITAS:",
        f"  ROE: {pct(p.get('roe_pct'))} | Net Margin: {pct(p.get('net_margin_pct'))} | EBITDA Margin: {pct(p.get('ebitda_margin_pct'))}",
        "",
        f"PERTUMBUHAN YoY: {g.get('verdict','?')}",
        f"  Revenue: {pct(g.get('revenue_yoy_pct'))} | Earnings: {pct(g.get('earnings_yoy_pct'))}",
        "",
        f"KESEHATAN: {h.get('verdict','?')}",
        f"  D/E: {h.get('de_ratio','N/A')} | Current Ratio: {h.get('current_ratio','N/A')} | FCF: {rp(h.get('free_cash_flow'))}",
        "",
        f"DIVIDEN: Yield {pct(d.get('yield_pct'))} | Payout {pct(d.get('payout_ratio_pct'))}",
    ]
    if q_str:    lines += ["", q_str]
    if rev_trend: lines.append(rev_trend)
    return "\n".join(lines)


# ── Prompt builder ────────────────────────────────────────────────────────────
def build_prompt(
    stock_code: str,
    company_names: list[str],
    articles: list[dict],
    price_data: dict,
    macro_context: str = "",
    fundamental_context: str = "",
) -> str:
    company_label = company_names[0] if company_names else stock_code
    has_articles  = len(articles) > 0

    if price_data.get("available"):
        price_section = (
            f"Harga: {_fmt_price(price_data.get('current_price'))} ({price_data.get('last_date','?')}) | "
            f"1M: {_fmt_pct(price_data.get('change_1m_pct'))} | "
            f"6M: {_fmt_pct(price_data.get('change_6m_pct'))} | "
            f"1Y: {_fmt_pct(price_data.get('change_1y_pct'))}"
        )
    else:
        price_section = "Data harga tidak tersedia."

    fund_section = f"\n--- FUNDAMENTAL KEUANGAN ---\n{fundamental_context}\n" if fundamental_context else ""

    json_schema = """{
  "price_trend": {"direction":"NAIK/TURUN/SIDEWAYS","momentum":"KUAT/SEDANG/LEMAH","assessment":"<1-2 kalimat>"},
  "sentiment": {"overall":"POSITIF/NEGATIF/NETRAL","positive_rate":0.0,"negative_rate":0.0,"neutral_rate":0.0,"positive_count":0,"negative_count":0,"neutral_count":0},
  "short_term": {"signal":"BELI/TAHAN/JUAL","outlook":"BULLISH/BEARISH/SIDEWAYS","confidence":0.0,"timeframe":"1-4 minggu","reasoning":"<dari berita + harga>","entry_note":"<strategi entry>"},
  "long_term": {"signal":"BELI/TAHAN/JUAL","outlook":"BULLISH/BEARISH/SIDEWAYS","confidence":0.0,"timeframe":"6-12 bulan","reasoning":"<fundamental + corporate action>","entry_note":"<target>"},
  "investment_timing": {"signal":"GOOD_TIME_TO_BUY/WAIT_FOR_DIP/ACCUMULATE/TAKE_PROFIT/AVOID","label":"<Bahasa Indonesia>","score":0,"reasoning":"<sintesis>"},
  "fundamental": {"valuation_verdict":"SANGAT MURAH/MURAH/WAJAR/MAHAL/SANGAT MAHAL","financial_health":"SEHAT/CUKUP/LEMAH/KRITIS","growth_quality":"KUAT/MODERAT/MELAMBAT/MENURUN","divergence_flag":null,"cross_validation":"<1-2 kalimat>"},
  "key_factors": ["<faktor 1>","<faktor 2>","<faktor 3>"],
  "risks": ["<risiko 1>","<risiko 2>"],
  "key_events": ["<event korporasi jika ada>"],
  "recommendation": "BELI/TAHAN/JUAL",
  "summary": "<sintesis 1-2 kalimat>"
}"""

    if has_articles:
        articles_text = _format_articles(articles)
        return f"""Kamu adalah analis saham senior IDX Indonesia. Analisis {stock_code} ({company_label}).

PRIORITAS ANALISIS:
1. [***] Corporate Action & Laporan Keuangan — penggerak nilai intrinsik
2. [**]  Smart Money — aksi fund manager & insider
3. [*]   Sektor/Makro — suku bunga, komoditas, regulasi
4. [ ]   Rekomendasi Analis — opini, bobot sedang
5. [NOISE] Pergerakan harian — bobot rendah

DATA HARGA: {price_section}
{fund_section}
BERITA {stock_code} ({len(articles)} artikel):
{articles_text}

INSTRUKSI:
1. Identifikasi berita [***] — penggerak nilai intrinsik utama
2. Cek Smart Money — net buy atau sell?
3. Cross-validate dengan fundamental data
4. Tentukan timing & rekomendasi

Output HANYA JSON murni (tanpa markdown, tanpa penjelasan):
{json_schema}

Rules: positive_rate+negative_rate+neutral_rate=1.0 | score=bilangan bulat 0-100 | confidence=0.0-1.0
{macro_context}"""
    else:
        return f"""Kamu adalah analis saham senior IDX Indonesia. Tidak ada berita terbaru untuk {stock_code} ({company_label}).
Analisis berdasarkan data fundamental, tren harga, dan pengetahuan tentang perusahaan ini.

DATA HARGA: {price_section}
{fund_section}
Gunakan pengetahuanmu tentang {company_label}: sektor bisnis, kompetitor, model bisnis di Indonesia.

Output HANYA JSON murni:
{json_schema}

Rules: positive_rate+negative_rate+neutral_rate=1.0 | score=bilangan bulat 0-100 | confidence=0.0-1.0
{macro_context}"""


# ── JSON extraction ───────────────────────────────────────────────────────────
def _try_parse(text: str) -> Optional[dict]:
    try:
        return json.loads(text.strip())
    except Exception:
        return None

def extract_json(text: str) -> Optional[dict]:
    if not text: return None
    result = _try_parse(text)
    if result: return result
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    result = _try_parse(cleaned)
    if result: return result
    brace_start = cleaned.find('{')
    if brace_start != -1:
        depth, brace_end, in_str, esc = 0, -1, False, False
        for i, ch in enumerate(cleaned[brace_start:], start=brace_start):
            if esc: esc = False; continue
            if ch == '\\' and in_str: esc = True; continue
            if ch == '"': in_str = not in_str; continue
            if in_str: continue
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0: brace_end = i; break
        if brace_end != -1:
            result = _try_parse(cleaned[brace_start:brace_end + 1])
            if result: return result
    return None


# ── Normalization ─────────────────────────────────────────────────────────────
def normalize_analysis(data: dict) -> dict:
    def _cp(v, fb="N/A"):
        s = str(v) if v else fb
        return fb if s.startswith("[") or s.startswith("ISI") else s

    pt = data.get("price_trend") or {}
    data["price_trend"] = {
        "direction":  str(pt.get("direction",  "SIDEWAYS")),
        "momentum":   str(pt.get("momentum",   "SEDANG")),
        "assessment": str(pt.get("assessment", "Sedang dianalisis.")),
    }
    s  = data.get("sentiment") or {}
    pr = float(s.get("positive_rate", 0.33))
    nr = float(s.get("negative_rate", 0.33))
    nu = float(s.get("neutral_rate",  0.34))
    total = pr + nr + nu
    if total > 0 and abs(total - 1.0) > 0.05:
        pr, nr, nu = pr/total, nr/total, nu/total
    data["sentiment"] = {
        "overall":        str(s.get("overall", "NETRAL")),
        "positive_rate":  round(pr, 3), "negative_rate": round(nr, 3), "neutral_rate": round(nu, 3),
        "positive_count": int(s.get("positive_count", 0)),
        "negative_count": int(s.get("negative_count", 0)),
        "neutral_count":  int(s.get("neutral_count",  0)),
    }
    for key, default_tf in [("short_term","1-4 minggu"),("long_term","6-12 bulan")]:
        t = data.get(key) or {}
        data[key] = {
            "signal":     str(t.get("signal",    "TAHAN")),
            "outlook":    str(t.get("outlook",   "SIDEWAYS")),
            "confidence": float(t.get("confidence", 0.5)),
            "timeframe":  str(t.get("timeframe", default_tf)),
            "reasoning":  str(t.get("reasoning", "Tidak ada data.")),
            "entry_note": str(t.get("entry_note", "")),
        }
    it    = data.get("investment_timing") or {}
    score = max(0, min(100, int(it.get("score", 50))))
    data["investment_timing"] = {
        "signal":    str(it.get("signal",    "WAIT_FOR_DIP")),
        "label":     str(it.get("label",     "Tunggu Koreksi")),
        "score":     score,
        "reasoning": str(it.get("reasoning", "Tidak ada data.")),
    }
    fund = data.get("fundamental") or {}
    data["fundamental"] = {
        "valuation_verdict": _cp(fund.get("valuation_verdict")),
        "financial_health":  _cp(fund.get("financial_health")),
        "growth_quality":    _cp(fund.get("growth_quality")),
        "divergence_flag":   fund.get("divergence_flag"),
        "cross_validation":  _cp(fund.get("cross_validation"), ""),
    }
    kf = data.get("key_factors", [])
    data["key_factors"] = [str(f) for f in kf if f and not str(f).startswith("[")] if isinstance(kf, list) else []
    rk = data.get("risks", [])
    data["risks"]       = [str(r) for r in rk] if isinstance(rk, list) else []
    ke = data.get("key_events", [])
    data["key_events"]  = [str(e) for e in ke if e and not str(e).startswith("[")] if isinstance(ke, list) else []
    data["recommendation"] = str(data.get("recommendation", "TAHAN"))
    data["summary"]        = str(data.get("summary", "Analisis tidak tersedia."))
    return data


# ── Provider: Groq ────────────────────────────────────────────────────────────
async def _call_groq(prompt: str) -> str:
    """Call Groq Llama 3.3 70B with JSON mode. Raises on error."""
    client = _get_groq()
    if not client:
        raise RuntimeError("GROQ_API_KEY not configured")
    import asyncio
    loop = asyncio.get_running_loop()
    def _sync():
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Kamu adalah analis saham senior IDX Indonesia. "
                        "Selalu respond dalam format JSON yang valid dan lengkap. "
                        "Jangan tambahkan teks atau penjelasan di luar JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=4096,
        )
        return resp.choices[0].message.content or ""
    return await loop.run_in_executor(None, _sync)


# ── Provider: Gemini ──────────────────────────────────────────────────────────
async def _call_gemini(prompt: str) -> str:
    """Call Gemini with JSON mime type. Raises on error."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not configured")
    model    = genai.GenerativeModel(GEMINI_MODEL)
    response = await model.generate_content_async(
        prompt,
        generation_config=genai.types.GenerationConfig(
            temperature=0.25,
            max_output_tokens=2048,
            response_mime_type="application/json",
        ),
    )
    return response.text or ""


# ── Main analyze function ─────────────────────────────────────────────────────
async def analyze_with_gemini(
    stock_code: str,
    company_names: list[str],
    articles: list[dict],
    price_data: dict,
    macro_context: str = "",
    fundamental_data: Optional[dict] = None,
) -> dict:
    """
    Run AI analysis. Try Groq first → fallback to Gemini.
    Raises RuntimeError only if BOTH providers fail.
    """
    company_label = company_names[0] if company_names else stock_code
    fund_ctx = ""
    if fundamental_data and fundamental_data.get("available"):
        fund_ctx = build_fundamental_context(fundamental_data)

    prompt = build_prompt(
        stock_code, company_names, articles, price_data,
        macro_context, fund_ctx,
    )

    raw_text = None
    provider_used = None
    errors = []

    # Determine order: groq first if AI_PRIMARY == 'groq'
    providers = (
        [("groq", _call_groq), ("gemini", _call_gemini)]
        if AI_PRIMARY == "groq"
        else [("gemini", _call_gemini), ("groq", _call_groq)]
    )

    for name, caller in providers:
        try:
            logger.info(f"[{stock_code}] Trying {name} ({GROQ_MODEL if name=='groq' else GEMINI_MODEL})...")
            raw_text = await caller(prompt)
            provider_used = name
            logger.info(f"[{stock_code}] {name} succeeded ({len(raw_text)} chars)")
            break
        except Exception as e:
            err_msg = str(e)[:120]
            logger.warning(f"[{stock_code}] {name} failed: {err_msg}")
            errors.append(f"{name}: {err_msg}")

    if raw_text is None:
        raise RuntimeError(
            f"Both AI providers failed. Errors: {' | '.join(errors)}"
        )

    analysis = extract_json(raw_text)
    if not analysis:
        logger.warning(f"[{stock_code}] JSON parse failed from {provider_used}. Raw[:200]: {raw_text[:200]}")
        analysis = _fallback_analysis(stock_code, company_label)
    else:
        analysis = normalize_analysis(analysis)

    analysis["no_news"]        = len(articles) == 0
    analysis["ai_provider"]    = provider_used
    return analysis


def _fallback_analysis(stock_code: str, company_label: str) -> dict:
    return {
        "price_trend":       {"direction":"N/A","momentum":"N/A","assessment":"Gagal diproses."},
        "sentiment":         {"overall":"NETRAL","positive_rate":0.33,"negative_rate":0.33,"neutral_rate":0.34,
                              "positive_count":0,"negative_count":0,"neutral_count":0},
        "short_term":        {"signal":"TAHAN","outlook":"SIDEWAYS","confidence":0.4,"timeframe":"1-4 minggu",
                              "reasoning":"Analisis gagal.","entry_note":""},
        "long_term":         {"signal":"TAHAN","outlook":"SIDEWAYS","confidence":0.4,"timeframe":"6-12 bulan",
                              "reasoning":"Analisis gagal.","entry_note":""},
        "investment_timing": {"signal":"WAIT_FOR_DIP","label":"Coba Analisa Ulang","score":50,
                              "reasoning":"Gagal memproses respons AI."},
        "fundamental":       {"valuation_verdict":"N/A","financial_health":"N/A","growth_quality":"N/A",
                              "divergence_flag":None,"cross_validation":""},
        "key_factors":       [f"Klik Analisa sekali lagi untuk {company_label}"],
        "risks":             ["Hasil AI tidak dapat diproses — coba ulang"],
        "key_events":        [],
        "recommendation":    "TAHAN",
        "summary":           f"Analisis {stock_code} gagal. Silakan coba lagi.",
        "_parse_error":      True,
        "no_news":           False,
        "ai_provider":       "none",
    }
