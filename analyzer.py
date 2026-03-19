import httpx
import json
import re
from typing import Optional

OLLAMA_BASE_URL = "http://localhost:11434"
MODEL_NAME = "qwen3:4b"


def _fmt_price(p) -> str:
    if p is None:
        return "N/A"
    return f"Rp {int(p):,}".replace(",", ".")


def _fmt_pct(p) -> str:
    if p is None:
        return "N/A"
    sign = "+" if p > 0 else ""
    return f"{sign}{p:.2f}%"


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ─────────────────────────────────────────────────────────────────────────────

def _format_articles_by_category(articles: list[dict]) -> str:
    """
    Format artikel menjadi teks input AI dengan label kategori.
    Artikel Corporate Action & Fundamental diberi tanda khusus.
    """
    PRIORITY_MARK = {
        "corporate_action":     "[★★★ CORPORATE ACTION]",
        "fundamental":          "[★★★ LAPORAN KEUANGAN]",
        "ownership":            "[★★☆ SMART MONEY]",
        "sector_macro":         "[★☆☆ SEKTOR/MAKRO]",
        "analyst_recommendation": "[☆☆☆ REKOMENDASI]",
        "market_noise":         "[NOISE - bobot rendah]",
    }
    lines = []
    for i, art in enumerate(articles[:15], 1):
        cat_id = art.get("category", "general")
        mark   = PRIORITY_MARK.get(cat_id, "")
        lines.append(f"[{i}]{mark} {art['title']} ({art['source']}, {art['published']})")
    return "\n".join(lines)


def build_analysis_prompt(
    stock_code: str,
    company_names: list[str],
    articles: list[dict],
    price_data: dict,
    macro_context: str = "",
) -> str:
    company_label = company_names[0] if company_names else stock_code

    if price_data.get("available"):
        price_section = (
            f"Harga sekarang: {_fmt_price(price_data.get('current_price'))} ({price_data.get('last_date','?')})\n"
            f"1 bulan lalu: {_fmt_price(price_data.get('price_1m_ago'))} ({_fmt_pct(price_data.get('change_1m_pct'))})\n"
            f"6 bulan lalu: {_fmt_price(price_data.get('price_6m_ago'))} ({_fmt_pct(price_data.get('change_6m_pct'))})\n"
            f"1 tahun lalu: {_fmt_price(price_data.get('price_1y_ago'))} ({_fmt_pct(price_data.get('change_1y_pct'))})"
        )
        if price_data.get("pe_ratio"):
            price_section += f"\nP/E Ratio: {price_data['pe_ratio']:.1f}x"
        if price_data.get("div_yield"):
            price_section += f"\nDividend Yield: {price_data['div_yield']*100:.2f}%"
    else:
        price_section = "Data harga tidak tersedia."

    articles_text = _format_articles_by_category(articles)

    prompt = f"""Kamu adalah analis saham senior IDX. Analisis {stock_code} ({company_label}) menggunakan framework analis profesional.

FRAMEWORK PRIORITAS ANALISIS (dari yang paling menentukan nilai intrinsik):
1. ★★★ CORPORATE ACTION — akuisisi, merger, obligasi, rights issue, dividen, RUPS, kuasi reorganisasi
2. ★★★ LAPORAN KEUANGAN — laba/rugi nyata, revenue, margin, kinerja kuartalan/tahunan
3. ★★☆ SMART MONEY — aksi jual/beli oleh fund manager, pemegang saham besar, insider
4. ★☆☆ SEKTOR/MAKRO — harga komoditas, regulasi, suku bunga yang berdampak langsung
5. ☆☆☆ REKOMENDASI ANALIS — bersifat opini, bobot sedang
6. [NOISE] — pergerakan harian, headline generik — abaikan atau bobot rendah

DATA HARGA:
{price_section}

BERITA {stock_code} ({len(articles)} artikel, diurutkan by relevansi):
{articles_text}

LANGKAH ANALISIS:
1. Identifikasi berita ★★★ (Corporate Action & Keuangan) — ini penggerak nilai intrinsik
2. Periksa sinyal Smart Money — apakah asing/institusi net buy atau net sell?
3. Konfirmasi dengan tren harga historis
4. Hitung sentimen NYATA per kategori artikel
5. Sintesis: apakah ada perubahan strategis besar? (e.g. diversifikasi bisnis, restructuring)
6. Tentukan timing investasi berdasarkan gabungan semua layer

Isi nilai JSON berikut dengan analisis NYATA (ganti [ISI:...] dengan konten asli):
{{"price_trend":{{"direction":"[ISI: NAIK atau TURUN atau SIDEWAYS]","momentum":"[ISI: KUAT atau SEDANG atau LEMAH]","assessment":"[ISI: tren harga + korelasinya dengan corporate actions]"}},"sentiment":{{"overall":"[ISI: POSITIF atau NEGATIF atau NETRAL]","positive_rate":0.0,"negative_rate":0.0,"neutral_rate":0.0,"positive_count":0,"negative_count":0,"neutral_count":0}},"short_term":{{"signal":"[ISI: BELI atau TAHAN atau JUAL]","outlook":"[ISI: BULLISH atau BEARISH atau SIDEWAYS]","confidence":0.0,"timeframe":"1-4 minggu","reasoning":"[ISI: alasan dari berita ★★★ + sinyal harga]","entry_note":"[ISI: strategi entry konkret]"}},"long_term":{{"signal":"[ISI: BELI atau TAHAN atau JUAL]","outlook":"[ISI: BULLISH atau BEARISH atau SIDEWAYS]","confidence":0.0,"timeframe":"6-12 bulan","reasoning":"[ISI: dampak corporate action + fundamental jangka panjang]","entry_note":"[ISI: target price atau strategi panjang]"}},"investment_timing":{{"signal":"[ISI: GOOD_TIME_TO_BUY atau WAIT_FOR_DIP atau ACCUMULATE atau TAKE_PROFIT atau AVOID]","label":"[ISI: label Bahasa Indonesia]","score":0,"reasoning":"[ISI: sintesis semua layer analisis]"}},"key_factors":["[ISI: corporate action/fundamental terpenting]","[ISI: faktor kedua]","[ISI: sinyal smart money atau sektor]"],"risks":["[ISI: risiko struktural atau operasional]","[ISI: risiko eksternal/makro]"],"key_events":["[ISI: event korporasi besar jika ada]","[ISI: event keuangan signifikan]"],"recommendation":"[ISI: BELI atau TAHAN atau JUAL]","summary":"[ISI: sintesis 1-2 kalimat yang menyebut corporate action & fundamental terpenting]"}}

ATURAN:
- Ganti SEMUA [ISI:...] dengan konten nyata yang mengacu pada berita spesifik
- Prioritaskan berita ★★★ dalam reasoning & key_factors
- positive_rate + negative_rate + neutral_rate = 1.0 (hitung dari jumlah berita)
- score = 0-100 bilangan bulat
- HANYA JSON murni"""
    if macro_context:
        prompt += f"\n{macro_context}"
    return prompt


def build_no_news_prompt(
    stock_code: str,
    company_names: list[str],
    price_data: dict,
    macro_context: str = "",
) -> str:
    company_label = company_names[0] if company_names else stock_code

    if price_data.get("available"):
        price_section = (
            f"Harga sekarang: {_fmt_price(price_data.get('current_price'))}\n"
            f"1 bulan lalu: {_fmt_price(price_data.get('price_1m_ago'))} ({_fmt_pct(price_data.get('change_1m_pct'))})\n"
            f"6 bulan lalu: {_fmt_price(price_data.get('price_6m_ago'))} ({_fmt_pct(price_data.get('change_6m_pct'))})\n"
            f"1 tahun lalu: {_fmt_price(price_data.get('price_1y_ago'))} ({_fmt_pct(price_data.get('change_1y_pct'))})"
        )
    else:
        price_section = "Data harga tidak tersedia."

    return f"""Kamu adalah analis saham senior IDX. Tidak ada berita terkini untuk {stock_code} ({company_label}).
Analisis berdasarkan tren harga historis dan pengetahuan fundamental perusahaan.

DATA HARGA:
{price_section}

Gunakan pengetahuan kamu tentang {company_label}: sektor bisnis, kompetitor, model bisnis, posisi pasar.

Isi nilai JSON berikut dengan analisis NYATA (ganti [ISI:...] dengan konten asli):
{{"price_trend":{{"direction":"[ISI: NAIK atau TURUN atau SIDEWAYS]","momentum":"[ISI: KUAT atau SEDANG atau LEMAH]","assessment":"[ISI: jelaskan tren harga dan apa artinya]"}},"sentiment":{{"overall":"[ISI: POSITIF atau NEGATIF atau NETRAL]","positive_rate":0.4,"negative_rate":0.25,"neutral_rate":0.35,"positive_count":0,"negative_count":0,"neutral_count":0}},"short_term":{{"signal":"[ISI: BELI atau TAHAN atau JUAL]","outlook":"[ISI: BULLISH atau BEARISH atau SIDEWAYS]","confidence":0.0,"timeframe":"1-4 minggu","reasoning":"[ISI: tren harga + kondisi sektoral]","entry_note":"[ISI: strategi entry]"}},"long_term":{{"signal":"[ISI: BELI atau TAHAN atau JUAL]","outlook":"[ISI: BULLISH atau BEARISH atau SIDEWAYS]","confidence":0.0,"timeframe":"6-12 bulan","reasoning":"[ISI: fundamental {company_label} + potensi bisnis]","entry_note":"[ISI: target dan strategi]"}},"investment_timing":{{"signal":"[ISI: GOOD_TIME_TO_BUY atau WAIT_FOR_DIP atau ACCUMULATE atau TAKE_PROFIT atau AVOID]","label":"[ISI: label Bahasa Indonesia]","score":0,"reasoning":"[ISI: alasan timing berdasarkan tren]"}},"key_factors":["[ISI: fundamental utama {company_label}]","[ISI: keunggulan kompetitif]","[ISI: kondisi sektor]"],"risks":["[ISI: risiko bisnis utama]","[ISI: risiko makro/regulasi]"],"key_events":[],"recommendation":"[ISI: BELI atau TAHAN atau JUAL]","summary":"[ISI: ringkasan fundamental dan timing untuk {company_label}]"}}

Ganti SEMUA [ISI:...] dengan konten nyata. HANYA JSON."""
    if macro_context:
        return base + f"\n{macro_context}"
    return base


# ─────────────────────────────────────────────────────────────────────────────
# JSON Extraction & Repair
# ─────────────────────────────────────────────────────────────────────────────

def _try_parse(text: str) -> Optional[dict]:
    """Coba parse JSON, return None jika gagal."""
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return None


def _repair_truncated_json(text: str) -> Optional[dict]:
    """
    Coba perbaiki JSON yang terpotong karena num_predict habis.
    Strategi: temukan koma terakhir di level atas, cut di sana, tutup brace.
    """
    brace_start = text.find('{')
    if brace_start == -1:
        return None

    working = text[brace_start:]

    # Cari posisi koma terakhir di depth=1 (level top-level object)
    depth = 0
    in_string = False
    escape_next = False
    last_safe_cut = -1

    for i, ch in enumerate(working):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                last_safe_cut = i  # JSON sudah complete
        elif ch == ',' and depth == 1:
            last_safe_cut = i  # Koma setelah key-value lengkap

    if last_safe_cut <= 0:
        return None

    # Versi 1: potong di koma terakhir, tutup brace
    partial = working[:last_safe_cut] + '}'
    result = _try_parse(partial)
    if result:
        return result

    # Versi 2: kalau ada unclosed bracket [...], tutup juga
    open_brackets = partial.count('[') - partial.count(']')
    if open_brackets > 0:
        partial2 = working[:last_safe_cut] + ']' * open_brackets + '}'
        result = _try_parse(partial2)
        if result:
            return result

    return None


def extract_json_from_response(text: str) -> Optional[dict]:
    """
    Ekstrak JSON dari response Ollama secara robust.
    Urutan: direct parse → strip think block → bracket match → repair truncated.
    """
    if not text:
        return None

    # 1. Direct parse
    result = _try_parse(text)
    if result:
        return result

    # 2. Hapus <think>...</think> block (Qwen3 thinking mode)
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    result = _try_parse(cleaned)
    if result:
        return result

    # 3. Bracket matching (cari JSON { ... } paling luar)
    brace_start = cleaned.find('{')
    if brace_start != -1:
        depth, brace_end = 0, -1
        in_str, esc = False, False
        for i, ch in enumerate(cleaned[brace_start:], start=brace_start):
            if esc:
                esc = False; continue
            if ch == '\\' and in_str:
                esc = True; continue
            if ch == '"':
                in_str = not in_str; continue
            if in_str:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    brace_end = i; break
        if brace_end != -1:
            result = _try_parse(cleaned[brace_start:brace_end + 1])
            if result:
                return result

    # 4. Repair truncated JSON (last resort)
    return _repair_truncated_json(cleaned)


# ─────────────────────────────────────────────────────────────────────────────
# Main analyzer
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_with_ollama(
    stock_code: str,
    company_names: list[str],
    articles: list[dict],
    price_data: dict,
    macro_context: str = "",
) -> dict:
    has_articles = len(articles) > 0
    company_label = company_names[0] if company_names else stock_code

    if has_articles:
        prompt = build_analysis_prompt(stock_code, company_names, articles, price_data, macro_context)
    else:
        prompt = build_no_news_prompt(stock_code, company_names, price_data, macro_context)

    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,   # Matikan thinking mode Qwen3 agar hemat token
        "format": "json", # Ollama grammar sampler → paksa output valid JSON
        "options": {
            "temperature": 0.3,
            "num_predict": 3000,  # ← NAIK dari 1200 ke 3000 (fix truncation)
            "num_ctx": 8192,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=240.0) as client:
            resp = await client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_response = data.get("message", {}).get("content", "")

        analysis = extract_json_from_response(raw_response)

        if not analysis:
            analysis = _fallback_analysis(stock_code, company_label, raw_response)
        else:
            # Validasi & normalisasi field kritis supaya UI tidak crash
            analysis = _normalize_analysis(analysis, stock_code, company_label)

        analysis["no_news"] = not has_articles
        return analysis

    except httpx.ConnectError:
        raise RuntimeError("Tidak dapat terhubung ke Ollama. Pastikan Ollama sedang berjalan.")
    except httpx.TimeoutException:
        raise RuntimeError("Ollama timeout. Model terlalu lama. Coba lagi.")
    except Exception as e:
        raise RuntimeError(f"Error Ollama: {str(e)}")


def _normalize_analysis(data: dict, stock_code: str, company_label: str) -> dict:
    """
    Pastikan semua field yang dibutuhkan UI ada dan bertipe benar.
    Jika ada field yang missing/None, isi dengan default sensibel.
    """
    # price_trend
    pt = data.get("price_trend") or {}
    if not isinstance(pt, dict):
        pt = {}
    data["price_trend"] = {
        "direction":  str(pt.get("direction", "SIDEWAYS")),
        "momentum":   str(pt.get("momentum",  "SEDANG")),
        "assessment": str(pt.get("assessment", "Data harga sedang dianalisis.")),
    }

    # sentiment
    s = data.get("sentiment") or {}
    if not isinstance(s, dict):
        s = {}
    pr = float(s.get("positive_rate", 0.33))
    nr = float(s.get("negative_rate", 0.33))
    nu = float(s.get("neutral_rate",  0.34))
    total = pr + nr + nu
    if total > 0 and abs(total - 1.0) > 0.05:
        pr, nr, nu = pr/total, nr/total, nu/total
    data["sentiment"] = {
        "overall":        str(s.get("overall", "NETRAL")),
        "positive_rate":  round(pr, 3),
        "negative_rate":  round(nr, 3),
        "neutral_rate":   round(nu, 3),
        "positive_count": int(s.get("positive_count", 0)),
        "negative_count": int(s.get("negative_count", 0)),
        "neutral_count":  int(s.get("neutral_count",  0)),
    }

    # short_term / long_term
    for key, default_tf in [("short_term", "1-4 minggu"), ("long_term", "6-12 bulan")]:
        t = data.get(key) or {}
        if not isinstance(t, dict):
            t = {}
        data[key] = {
            "signal":     str(t.get("signal",    "TAHAN")),
            "outlook":    str(t.get("outlook",   "SIDEWAYS")),
            "confidence": float(t.get("confidence", 0.5)),
            "timeframe":  str(t.get("timeframe", default_tf)),
            "reasoning":  str(t.get("reasoning", "Tidak ada data.")),
            "entry_note": str(t.get("entry_note", "")),
        }

    # investment_timing
    it = data.get("investment_timing") or {}
    if not isinstance(it, dict):
        it = {}
    score = int(it.get("score", 50))
    data["investment_timing"] = {
        "signal":    str(it.get("signal",    "WAIT_FOR_DIP")),
        "label":     str(it.get("label",     "Tunggu Koreksi")),
        "score":     max(0, min(100, score)),
        "reasoning": str(it.get("reasoning", "Tidak ada data.")),
    }

    # key_factors & risks — pastikan list of strings
    kf = data.get("key_factors", [])
    data["key_factors"] = [str(f) for f in kf] if isinstance(kf, list) else []

    rk = data.get("risks", [])
    data["risks"] = [str(r) for r in rk] if isinstance(rk, list) else []

    # key_events — corporate actions / major events identified by AI
    # Filter out unfilled [ISI:...] placeholders
    ke = data.get("key_events", [])
    data["key_events"] = (
        [str(e) for e in ke if e and not str(e).startswith("[ISI")]
        if isinstance(ke, list) else []
    )

    # recommendation & summary
    data["recommendation"] = str(data.get("recommendation", "TAHAN"))
    data["summary"]        = str(data.get("summary", "Analisis tidak tersedia."))

    return data


def _fallback_analysis(stock_code: str, company_label: str, raw: str) -> dict:
    """Fallback terstruktur jika semua metode parsing gagal."""
    return {
        "price_trend": {"direction": "N/A", "momentum": "N/A", "assessment": "Gagal diproses."},
        "sentiment": {
            "overall": "NETRAL",
            "positive_rate": 0.33, "negative_rate": 0.33, "neutral_rate": 0.34,
            "positive_count": 0, "negative_count": 0, "neutral_count": 0,
        },
        "short_term": {
            "signal": "TAHAN", "outlook": "SIDEWAYS", "confidence": 0.4,
            "timeframe": "1-4 minggu", "reasoning": "Analisis gagal, coba klik Analisa lagi.",
            "entry_note": "",
        },
        "long_term": {
            "signal": "TAHAN", "outlook": "SIDEWAYS", "confidence": 0.4,
            "timeframe": "6-12 bulan", "reasoning": "Analisis gagal, coba klik Analisa lagi.",
            "entry_note": "",
        },
        "investment_timing": {
            "signal": "WAIT_FOR_DIP", "label": "Coba Analisa Ulang",
            "score": 50, "reasoning": "Gagal memproses response AI.",
        },
        "key_factors": [
            f"Klik tombol Analisa sekali lagi untuk {company_label}",
            "Terkadang model perlu percobaan kedua",
        ],
        "risks": ["Hasil AI tidak dapat diparsing — coba ulang"],
        "key_events": [],
        "recommendation": "TAHAN",
        "summary": f"Analisis {stock_code} gagal diproses. Silakan klik Analisa lagi.",
        "_parse_error": True,
        "_raw_preview": raw[:300],
    }

