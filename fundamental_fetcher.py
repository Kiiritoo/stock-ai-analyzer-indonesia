"""
fundamental_fetcher.py — Fetch data fundamental keuangan saham IDX.
Source: Yahoo Finance (yfinance)
Cache: 24 jam (laporan keuangan berubah secara kuartalan)
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import warnings
warnings.filterwarnings("ignore")

import yfinance as yf

_executor = ThreadPoolExecutor(max_workers=2)
_cache: dict = {}
CACHE_TTL = 86400  # 24 jam

def _get_cache(key: str) -> Optional[dict]:
    e = _cache.get(key)
    if e and time.time() - e["ts"] < CACHE_TTL:
        return e["data"]
    return None

def _set_cache(key: str, data: dict):
    _cache[key] = {"data": data, "ts": time.time()}


# ── Sector Benchmarks IDX Indonesia ──────────────────────────────────────────
# Berdasarkan rata-rata historis valuasi sektor di IDX
SECTOR_BENCHMARKS: dict = {
    "Financial Services":      {"pe": (8, 15),   "pb": (1.0, 2.5), "roe_min": 12, "label": "Perbankan/Keuangan"},
    "Technology":              {"pe": (25, 80),  "pb": (2.0, 10),  "roe_min": 10, "label": "Teknologi"},
    "Basic Materials":         {"pe": (6, 14),   "pb": (0.8, 3.0), "roe_min": 8,  "label": "Tambang/Material"},
    "Energy":                  {"pe": (5, 12),   "pb": (0.8, 2.5), "roe_min": 10, "label": "Energi/Batu Bara"},
    "Consumer Defensive":      {"pe": (15, 35),  "pb": (2.0, 8.0), "roe_min": 15, "label": "Konsumer Staples"},
    "Consumer Cyclical":       {"pe": (10, 25),  "pb": (1.0, 5.0), "roe_min": 12, "label": "Konsumer Siklkal"},
    "Real Estate":             {"pe": (8, 20),   "pb": (0.5, 2.0), "roe_min": 6,  "label": "Properti"},
    "Communication Services":  {"pe": (12, 28),  "pb": (1.5, 5.0), "roe_min": 10, "label": "Telekomunikasi"},
    "Industrials":             {"pe": (10, 22),  "pb": (1.0, 3.5), "roe_min": 10, "label": "Industri"},
    "Utilities":               {"pe": (8, 18),   "pb": (0.8, 2.0), "roe_min": 8,  "label": "Utilitas"},
    "Healthcare":              {"pe": (15, 35),  "pb": (2.0, 6.0), "roe_min": 12, "label": "Kesehatan"},
    "Agriculture":             {"pe": (8, 18),   "pb": (0.8, 2.5), "roe_min": 8,  "label": "Perkebunan"},
    "_default":                {"pe": (10, 25),  "pb": (1.0, 4.0), "roe_min": 10, "label": "Umum"},
}


# ── Helpers ───────────────────────────────────────────────────────────────────
def _f(val, mult=1.0) -> Optional[float]:
    try:
        if val is None: return None
        f = float(val)
        if f != f: return None  # NaN
        return round(f * mult, 2)
    except (TypeError, ValueError):
        return None

def _df_to_compact(df, metrics: list, max_cols: int = 4) -> dict:
    """Convert yfinance DataFrame subset to JSON-serializable dict."""
    if df is None or df.empty:
        return {}
    out = {}
    cols = list(df.columns)[:max_cols]
    for metric in metrics:
        if metric not in df.index:
            continue
        row = {}
        for col in cols:
            try:
                val = df.loc[metric, col]
                key = str(col.date()) if hasattr(col, 'date') else str(col)
                row[key] = round(float(val), 0) if val == val else None  # noqa
            except Exception:
                pass
        out[metric] = row
    return out

def _quarterly_trend(q_df) -> list:
    if q_df is None or q_df.empty or 'Net Income' not in q_df.index:
        return []
    try:
        ni = q_df.loc['Net Income']
        result = []
        for col in ni.index:
            v = ni[col]
            if v == v:  # not NaN
                result.append({"period": str(col.date()), "value": round(float(v), 0)})
        return sorted(result, key=lambda x: x["period"])  # oldest first
    except Exception:
        return []

def _assess_valuation(pe: Optional[float], sector: str) -> str:
    bench = SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS["_default"])
    lo, hi = bench["pe"]
    if pe is None: return "N/A"
    if pe < lo * 0.7:  return "SANGAT MURAH"
    if pe < lo:        return "MURAH"
    if pe <= hi:       return "WAJAR"
    if pe <= hi * 1.4: return "MAHAL"
    return "SANGAT MAHAL"

def _assess_health(de: Optional[float], cr: Optional[float], fcf: Optional[float]) -> str:
    score = 0
    if de  is not None: score += (2 if de > 2.0 else 1 if de > 1.0 else 0)
    if cr  is not None: score += (2 if cr < 1.0 else 1 if cr < 1.5 else 0)
    if fcf is not None: score += (1 if fcf < 0 else 0)
    return ["SEHAT", "CUKUP", "LEMAH", "LEMAH", "KRITIS"][min(score, 4)]

def _assess_growth(rev_g: Optional[float], earn_g: Optional[float]) -> str:
    scores = []
    for g in [rev_g, earn_g]:
        if g is None: continue
        if g > 20:   scores.append(3)
        elif g > 10: scores.append(2)
        elif g > 0:  scores.append(1)
        else:        scores.append(0)
    if not scores: return "N/A"
    avg = sum(scores) / len(scores)
    return ["MENURUN", "MELAMBAT", "MODERAT", "KUAT"][min(int(avg), 3)]


# ── Main fetch function ───────────────────────────────────────────────────────
def _fetch_fundamentals_sync(ticker_symbol: str) -> dict:
    try:
        stock  = yf.Ticker(ticker_symbol)
        info   = stock.info or {}

        sector   = info.get('sector', '_default') or '_default'
        industry = info.get('industry', '') or ''
        bench    = SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS["_default"])

        # Valuation
        pe      = _f(info.get('trailingPE'))
        fwd_pe  = _f(info.get('forwardPE'))
        pb      = _f(info.get('priceToBook'))
        mktcap  = info.get('marketCap')
        ev      = info.get('enterpriseValue')
        shares  = info.get('sharesOutstanding')
        floats  = info.get('floatShares')

        # Profitability
        roe         = _f(info.get('returnOnEquity'), 100)
        net_margin  = _f(info.get('profitMargins'), 100)
        gross_m     = _f(info.get('grossMargins'), 100)
        ebitda_m    = _f(info.get('ebitdaMargins'), 100)

        # Growth
        rev_g  = _f(info.get('revenueGrowth'), 100)
        earn_g = _f(info.get('earningsGrowth'), 100)

        # Health
        de  = _f(info.get('debtToEquity'))
        cr  = _f(info.get('currentRatio'))

        # Dividends
        div_yield   = _f(info.get('dividendYield'), 100) or _f(info.get('trailingAnnualDividendYield'), 100)
        div_payout  = _f(info.get('dividendPayoutRatio'), 100)
        div_rate    = _f(info.get('dividendRate'))

        # Income statement (annual)
        income_metrics = ['Total Revenue', 'Gross Profit', 'Ebitda', 'Net Income',
                          'Operating Income', 'Operating Expense']
        try:
            fin = stock.financials
            income_stmt = _df_to_compact(fin, income_metrics, max_cols=4)
        except Exception:
            income_stmt = {}

        # Cash flow (annual)
        cf_metrics = ['Operating Cash Flow', 'Investing Cash Flow', 'Financing Cash Flow',
                      'Capital Expenditure', 'Free Cash Flow']
        try:
            cf_df = stock.cashflow
            cash_flow = _df_to_compact(cf_df, cf_metrics, max_cols=4)
        except Exception:
            cf_df = None
            cash_flow = {}

        # FCF latest
        fcf = None
        try:
            if cf_df is not None and "Free Cash Flow" in cf_df.index:
                v = cf_df.loc["Free Cash Flow"].iloc[0]
                fcf = round(float(v), 0) if v == v else None  # noqa
        except Exception:
            pass

        # Quarterly trend
        try:
            q_fin = stock.quarterly_financials
            quarterly = _quarterly_trend(q_fin)
        except Exception:
            quarterly = []

        # Assessments
        val_verdict    = _assess_valuation(pe, sector)
        health_verdict = _assess_health(de, cr, fcf)
        growth_verdict = _assess_growth(rev_g, earn_g)

        return {
            "available": True,
            "ticker":     ticker_symbol,
            "sector":     sector,
            "industry":   industry,
            "sector_label": bench["label"],
            "sector_benchmark": {
                "pe_range":  f"{bench['pe'][0]}–{bench['pe'][1]}x",
                "pb_range":  f"{bench['pb'][0]}–{bench['pb'][1]}x",
                "roe_min":   f">{bench['roe_min']}%",
            },
            "valuation": {
                "market_cap":       mktcap,
                "enterprise_value": ev,
                "shares_outstanding": shares,
                "float_shares":     floats,
                "pe_trailing":      pe,
                "pe_forward":       fwd_pe,
                "pb_ratio":         pb,
                "verdict":          val_verdict,
            },
            "profitability": {
                "roe_pct":          roe,
                "net_margin_pct":   net_margin,
                "gross_margin_pct": gross_m,
                "ebitda_margin_pct":ebitda_m,
            },
            "growth": {
                "revenue_yoy_pct":  rev_g,
                "earnings_yoy_pct": earn_g,
                "verdict":          growth_verdict,
            },
            "financial_health": {
                "de_ratio":           de,
                "current_ratio":      cr,
                "free_cash_flow":     fcf,
                "verdict":            health_verdict,
            },
            "dividends": {
                "yield_pct":        div_yield,
                "payout_ratio_pct": div_payout,
                "rate":             div_rate,
            },
            "income_statement":       income_stmt,
            "cash_flow":              cash_flow,
            "quarterly_net_income":   quarterly,
        }

    except Exception as e:
        return {"available": False, "error": str(e)[:120]}


async def fetch_fundamentals(stock_code: str) -> dict:
    """Async fundamental fetch. Cache 24 jam per ticker."""
    ticker = f"{stock_code.upper()}.JK"
    cached = _get_cache(ticker)
    if cached:
        return cached
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(_executor, _fetch_fundamentals_sync, ticker)
    if data.get("available"):
        _set_cache(ticker, data)
    return data


# ── Context builder untuk AI prompt ──────────────────────────────────────────
def build_fundamental_context(f: dict) -> str:
    """Return compressed fundamental string untuk diinjeksi ke prompt AI."""
    if not f or not f.get("available"):
        return ""

    v    = f.get("valuation", {})
    p    = f.get("profitability", {})
    g    = f.get("growth", {})
    h    = f.get("financial_health", {})
    d    = f.get("dividends", {})
    q    = f.get("quarterly_net_income", [])
    inc  = f.get("income_statement", {})
    bench  = f.get("sector_benchmark", {})
    s_lbl  = f.get("sector_label", "Umum")
    ticker = f.get("ticker", "")

    def rp(val):
        if val is None: return "N/A"
        for thr, suf in [(1e12,"T"), (1e9,"B"), (1e6,"M")]:
            if abs(val) >= thr: return f"Rp {val/thr:.1f}{suf}"
        return f"Rp {val:,.0f}"

    def pct(val):
        return f"{val:+.1f}%" if val is not None else "N/A"

    def xf(val):
        return f"{val:.1f}x" if val is not None else "N/A"

    # Quarterly trend string
    q_str = ""
    if q:
        last4 = q[-4:]
        parts = [f"{x['period'][-7:]}: {rp(x['value'])}" for x in last4]
        vals  = [x["value"] for x in last4 if x["value"] is not None]
        trend = "NAIK ✅" if len(vals) >= 2 and vals[-1] > vals[0] else (
                "TURUN ⚠" if len(vals) >= 2 and vals[-1] < vals[0] else "STABIL")
        q_str = f"Laba Bersih Kuartalan: {' → '.join(parts)} [{trend}]"

    # Revenue multi-year trend
    rev_trend = ""
    if "Total Revenue" in inc:
        rev = inc["Total Revenue"]
        dates = sorted(rev.keys())[-3:]
        pairs = [(d, rev[d]) for d in dates if rev.get(d) is not None]
        parts = []
        for i, (dt, val) in enumerate(pairs):
            if i > 0 and pairs[i-1][1]:
                g_pct = (val - pairs[i-1][1]) / abs(pairs[i-1][1]) * 100
                parts.append(f"{dt[:4]}: {rp(val)} ({g_pct:+.0f}%)")
            else:
                parts.append(f"{dt[:4]}: {rp(val)}")
        rev_trend = f"Revenue Tahunan: {' → '.join(parts)}"

    lines = [
        f"FUNDAMENTAL ({ticker} | Sektor: {s_lbl})",
        f"Benchmark: P/E {bench.get('pe_range','?')} | P/B {bench.get('pb_range','?')} | ROE {bench.get('roe_min','?')}",
        "",
        f"VALUASI → {v.get('verdict','?')}",
        f"  P/E: {xf(v.get('pe_trailing'))} | P/E Fwd: {xf(v.get('pe_forward'))} | P/B: {xf(v.get('pb_ratio'))} | MCap: {rp(v.get('market_cap'))}",
        "",
        f"PROFITABILITAS",
        f"  ROE: {pct(p.get('roe_pct'))} | Net Margin: {pct(p.get('net_margin_pct'))} | EBITDA Margin: {pct(p.get('ebitda_margin_pct'))}",
        "",
        f"PERTUMBUHAN YoY → {g.get('verdict','?')}",
        f"  Revenue: {pct(g.get('revenue_yoy_pct'))} | Earnings: {pct(g.get('earnings_yoy_pct'))}",
        "",
        f"KESEHATAN → {h.get('verdict','?')}",
        f"  D/E: {xf(h.get('de_ratio')).replace('x','')} | Current Ratio: {h.get('current_ratio','N/A')} | FCF: {rp(h.get('free_cash_flow'))}",
        "",
        f"DIVIDEN: Yield {pct(d.get('yield_pct'))} | Payout {pct(d.get('payout_ratio_pct'))}",
    ]
    if q_str:
        lines += ["", q_str]
    if rev_trend:
        lines.append(rev_trend)

    return "\n".join(lines)
