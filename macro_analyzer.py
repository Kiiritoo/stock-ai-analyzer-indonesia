"""
macro_analyzer.py — Ollama AI analysis untuk kondisi makro ekonomi Indonesia.
Cache: 30 menit (makro berubah lambat).
"""
import httpx
import json
import re
import time
from typing import Optional

OLLAMA_BASE_URL = "http://localhost:11434"
MODEL_NAME      = "qwen3:4b"
CACHE_TTL       = 1800   # 30 menit

_cache: dict = {}

def _get_cache() -> Optional[dict]:
    e = _cache.get("macro")
    if e and time.time() - e["ts"] < CACHE_TTL:
        return e["data"]
    return None

def _set_cache(data: dict):
    _cache["macro"] = {"data": data, "ts": time.time()}
    data["_cached_at"] = time.strftime("%H:%M:%S")


def _fmt(m: dict) -> str:
    if not m.get("available"):
        return "N/A"
    chg = m.get("change_pct", 0)
    sign = "+" if chg > 0 else ""
    return f"{m['price']:,.2f} {m.get('unit','')} ({sign}{chg:.2f}%)"


def build_macro_prompt(macro: dict) -> str:
    mkt    = macro.get("market", {})
    bi     = macro.get("bi_rate", {})
    fed    = macro.get("fed_rate", {})
    kmk    = macro.get("kurs_pajak", {})
    news   = macro.get("global_news", [])

    news_txt = "\n".join(f"- {n['title']} [{n['source']}]" for n in news[:8]) or "Tidak ada"

    return f"""Kamu adalah ekonom senior IDX. Analisis kondisi makro ekonomi Indonesia untuk rekomendasi investasi saham.

DATA MAKRO TERKINI:
IHSG      : {_fmt(mkt.get('IHSG', {}))}
USD/IDR   : {_fmt(mkt.get('USD_IDR', {}))}
BI Rate   : {bi.get('rate','?')}% ({bi.get('source','?')})
Fed Rate  : {fed.get('rate','?')}% ({fed.get('date','?')})
Kurs Pajak: {kmk.get('rate','N/A')} IDR/USD [{kmk.get('source','?')}]

KOMODITAS GLOBAL:
Emas      : {_fmt(mkt.get('Gold', {}))}
Minyak WTI: {_fmt(mkt.get('Oil', {}))}
Nikel LME : {_fmt(mkt.get('Nickel', {}))}
Batu Bara : {_fmt(mkt.get('Coal', {}))}

BERITA GLOBAL TERKINI:
{news_txt}

FRAMEWORK ANALISIS (gunakan sebagai landasan):
1. BI Rate NAIK → NEGATIF: Properti(BSDE,PWON), Teknologi(GOTO), Otomotif(ASII) | POSITIF: Bank(BBCA,BBRI,BMRI) [NIM melebar]
2. Rupiah LEMAH (USD/IDR naik) → NEGATIF: Importir(KLBF,UNVR,ASII) biaya impor+pajak naik | POSITIF: Eksportir(ADRO,PTBA,INCO,MDKA) revenue USD > IDR
3. Rupiah lemah tajam → psikologi: investor asing cenderung Net Sell big caps (BBCA,BBRI,TLKM) untuk lindungi nilai aset
4. Fed Rate tinggi → Capital Outflow dari EM termasuk Indonesia → IHSG tertekan
5. China lambat → permintaan batubara/nikel/CPO RI turun → NEGATIF mining
6. Harga komoditas naik → POSITIF untuk ADRO,PTBA,ANTM,INCO,MDKA

Berikan analisis JSON berikut dengan konten NYATA (ganti [ISI:...]):
{{"overall_market":{{"sentiment":"[ISI: BULLISH/BEARISH/SIDEWAYS]","signal":"[ISI: RISK_ON/RISK_OFF/NEUTRAL]","ihsg_outlook":"[ISI: 1 kalimat arah IHSG berdasarkan data]","rupiah_psychology":"[ISI: dampak psikologi investor asing dari kondisi Rupiah saat ini]","foreign_flow":"[ISI: prediksi arah Net Buy atau Net Sell asing + alasan singkat]"}},"sector_analysis":{{"banks":{{"sentiment":"[ISI: POSITIF/NEGATIF/NETRAL]","reasoning":"[ISI: 1 kalimat]","key_stocks":["[ISI: contoh saham]"]}},"property":{{"sentiment":"[ISI: POSITIF/NEGATIF/NETRAL]","reasoning":"[ISI: 1 kalimat]","key_stocks":["[ISI: contoh saham]"]}},"technology":{{"sentiment":"[ISI: POSITIF/NEGATIF/NETRAL]","reasoning":"[ISI: 1 kalimat]","key_stocks":["[ISI: contoh saham]"]}},"mining_commodity":{{"sentiment":"[ISI: POSITIF/NEGATIF/NETRAL]","reasoning":"[ISI: 1 kalimat]","key_stocks":["[ISI: contoh saham]"]}},"consumer_importer":{{"sentiment":"[ISI: POSITIF/NEGATIF/NETRAL]","reasoning":"[ISI: 1 kalimat]","key_stocks":["[ISI: contoh saham]"]}},"automotive":{{"sentiment":"[ISI: POSITIF/NEGATIF/NETRAL]","reasoning":"[ISI: 1 kalimat]","key_stocks":["[ISI: contoh saham]"]}}}},"key_watch":"[ISI: 1 hal terpenting dipantau investor hari ini]","global_risks":["[ISI: risiko 1]","[ISI: risiko 2]"],"opportunities":["[ISI: peluang 1]","[ISI: peluang 2]"]}}

ATURAN: Ganti SEMUA [ISI:...]. HANYA JSON murni."""


async def analyze_macro(macro: dict) -> dict:
    """Jalankan Ollama untuk analisis kondisi makro. Cache 30 menit."""
    cached = _get_cache()
    if cached:
        return cached

    prompt  = build_macro_prompt(macro)
    payload = {
        "model":   MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "stream":  False,
        "think":   False,
        "format":  "json",
        "options": {"temperature": 0.2, "num_predict": 1800, "num_ctx": 4096},
    }

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            resp.raise_for_status()
            raw = resp.json().get("message", {}).get("content", "")

        result = None
        try:
            result = json.loads(raw.strip())
        except Exception:
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                try:
                    result = json.loads(m.group())
                except Exception:
                    pass

        if not result:
            result = _fallback()

        _set_cache(result)
        return result

    except Exception as e:
        fb = _fallback()
        fb["_error"] = str(e)
        return fb


def _fallback() -> dict:
    return {
        "overall_market": {
            "sentiment": "SIDEWAYS", "signal": "NEUTRAL",
            "ihsg_outlook": "Analisis AI tidak tersedia saat ini.",
            "rupiah_psychology": "Data tidak dapat diproses.",
            "foreign_flow": "Tidak tersedia",
        },
        "sector_analysis": {},
        "key_watch": "Coba analisis ulang",
        "global_risks": [],
        "opportunities": [],
        "_error": "Analisis gagal",
    }
