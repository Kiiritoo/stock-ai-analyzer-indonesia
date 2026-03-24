"""
services/fundamental_fetcher.py — Fetch fundamental financial data from Yahoo Finance.
Ported from local app — identical output format for API compatibility.
"""
import asyncio
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

warnings.filterwarnings("ignore")
import yfinance as yf

_executor = ThreadPoolExecutor(max_workers=2)

# ── Sector Benchmarks IDX ─────────────────────────────────────────────────────
SECTOR_BENCHMARKS = {
    "Financial Services":      {"pe": (8,  15),  "pb": (1.0, 2.5), "roe_min": 12, "label": "Perbankan/Keuangan"},
    "Technology":              {"pe": (25, 80),  "pb": (2.0, 10),  "roe_min": 10, "label": "Teknologi"},
    "Basic Materials":         {"pe": (6,  14),  "pb": (0.8, 3.0), "roe_min": 8,  "label": "Tambang/Material"},
    "Energy":                  {"pe": (5,  12),  "pb": (0.8, 2.5), "roe_min": 10, "label": "Energi/Batu Bara"},
    "Consumer Defensive":      {"pe": (15, 35),  "pb": (2.0, 8.0), "roe_min": 15, "label": "Konsumer Staples"},
    "Consumer Cyclical":       {"pe": (10, 25),  "pb": (1.0, 5.0), "roe_min": 12, "label": "Konsumer Siklikal"},
    "Real Estate":             {"pe": (8,  20),  "pb": (0.5, 2.0), "roe_min": 6,  "label": "Properti"},
    "Communication Services":  {"pe": (12, 28),  "pb": (1.5, 5.0), "roe_min": 10, "label": "Telekomunikasi"},
    "Industrials":             {"pe": (10, 22),  "pb": (1.0, 3.5), "roe_min": 10, "label": "Industri"},
    "Utilities":               {"pe": (8,  18),  "pb": (0.8, 2.0), "roe_min": 8,  "label": "Utilitas"},
    "Healthcare":              {"pe": (15, 35),  "pb": (2.0, 6.0), "roe_min": 12, "label": "Kesehatan"},
    "Agriculture":             {"pe": (8,  18),  "pb": (0.8, 2.5), "roe_min": 8,  "label": "Perkebunan"},
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


def _df_to_compact_all(df, metrics: list) -> dict:
    if df is None or df.empty: return {}
    out = {}
    for metric in metrics:
        if metric not in df.index: continue
        row = {}
        for col in list(df.columns):
            try:
                val = df.loc[metric, col]
                key = str(col.date()) if hasattr(col, 'date') else str(col)
                if val == val:  # not NaN  # noqa
                    row[key] = round(float(val), 0)
            except Exception:
                pass
        if row:
            out[metric] = row
    return out


def _calc_ttm(df, metrics: list) -> dict:
    if df is None or df.empty: return {}
    ttm = {}
    for metric in metrics:
        if metric not in df.index: continue
        vals = []
        for col in list(df.columns)[:4]:
            try:
                v = df.loc[metric, col]
                if v == v: vals.append(float(v))  # noqa
            except Exception:
                pass
        if len(vals) >= 2:
            ttm[metric] = round(sum(vals), 0)
    return ttm


def _quarterly_trend(q_df) -> list:
    if q_df is None or q_df.empty or 'Net Income' not in q_df.index: return []
    try:
        ni = q_df.loc['Net Income']
        result = []
        for col in ni.index:
            v = ni[col]
            if v == v:  # not NaN  # noqa
                result.append({"period": str(col.date()), "value": round(float(v), 0)})
        return sorted(result, key=lambda x: x["period"])
    except Exception:
        return []


def _assess_valuation(pe, sector: str) -> str:
    bench = SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS["_default"])
    lo, hi = bench["pe"]
    if pe is None:   return "N/A"
    if pe < lo*0.7:  return "SANGAT MURAH"
    if pe < lo:      return "MURAH"
    if pe <= hi:     return "WAJAR"
    if pe <= hi*1.4: return "MAHAL"
    return "SANGAT MAHAL"


def _assess_health(de, cr, fcf) -> str:
    score = 0
    if de  is not None: score += (2 if de > 2.0 else 1 if de > 1.0 else 0)
    if cr  is not None: score += (2 if cr < 1.0 else 1 if cr < 1.5 else 0)
    if fcf is not None: score += (1 if fcf < 0 else 0)
    return ["SEHAT", "CUKUP", "LEMAH", "LEMAH", "KRITIS"][min(score, 4)]


def _assess_growth(rev_g, earn_g) -> str:
    scores = []
    for g in [rev_g, earn_g]:
        if g is None: continue
        scores.append(3 if g > 20 else 2 if g > 10 else 1 if g > 0 else 0)
    if not scores: return "N/A"
    avg = sum(scores) / len(scores)
    return ["MENURUN", "MELAMBAT", "MODERAT", "KUAT"][min(int(avg), 3)]


# ── Main sync fetch ───────────────────────────────────────────────────────────
def _fetch_sync(ticker_symbol: str) -> dict:
    try:
        stock  = yf.Ticker(ticker_symbol)
        info   = stock.info or {}
        sector   = info.get('sector', '_default') or '_default'
        industry = info.get('industry', '') or ''
        bench    = SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS["_default"])

        pe     = _f(info.get('trailingPE'))
        fwd_pe = _f(info.get('forwardPE'))
        pb     = _f(info.get('priceToBook'))
        mktcap = info.get('marketCap')
        ev     = info.get('enterpriseValue')
        shares = info.get('sharesOutstanding')
        floats = info.get('floatShares')
        roe        = _f(info.get('returnOnEquity'), 100)
        net_margin = _f(info.get('profitMargins'), 100)
        gross_m    = _f(info.get('grossMargins'), 100)
        ebitda_m   = _f(info.get('ebitdaMargins'), 100)
        rev_g  = _f(info.get('revenueGrowth'), 100)
        earn_g = _f(info.get('earningsGrowth'), 100)
        de     = _f(info.get('debtToEquity'))
        cr     = _f(info.get('currentRatio'))
        div_yield  = _f(info.get('dividendYield'), 100) or _f(info.get('trailingAnnualDividendYield'), 100)
        div_payout = _f(info.get('dividendPayoutRatio'), 100)
        div_rate   = _f(info.get('dividendRate'))

        income_metrics = ['Total Revenue', 'Gross Profit', 'Ebitda', 'Net Income',
                          'Operating Income', 'Operating Expense']
        cf_metrics     = ['Operating Cash Flow', 'Investing Cash Flow', 'Financing Cash Flow',
                          'Capital Expenditure', 'Free Cash Flow']

        # Annual
        fin_annual = None
        income_stmt = {}
        try:
            fin_annual = stock.financials
            if fin_annual is None or fin_annual.empty:
                fin_annual = stock.income_stmt
            income_stmt = _df_to_compact_all(fin_annual, income_metrics)
        except Exception:
            pass

        cf_annual = None
        cash_flow = {}
        try:
            cf_annual = stock.cashflow
            if cf_annual is None or cf_annual.empty:
                cf_annual = stock.cash_flow
            cash_flow = _df_to_compact_all(cf_annual, cf_metrics)
        except Exception:
            pass

        # FCF
        fcf = None
        try:
            if cf_annual is not None and "Free Cash Flow" in cf_annual.index:
                v = cf_annual.loc["Free Cash Flow"].iloc[0]
                fcf = round(float(v), 0) if v == v else None  # noqa
        except Exception:
            pass

        # Quarterly (2025 data)
        q_fin = None
        quarterly_income_stmt = {}
        try:
            q_fin = stock.quarterly_financials
            if q_fin is None or q_fin.empty:
                q_fin = stock.quarterly_income_stmt
            quarterly_income_stmt = _df_to_compact_all(q_fin, income_metrics)
        except Exception:
            pass

        q_cf = None
        quarterly_cashflow = {}
        try:
            q_cf = stock.quarterly_cashflow
            if q_cf is None or q_cf.empty:
                q_cf = stock.quarterly_cash_flow
            quarterly_cashflow = _df_to_compact_all(q_cf, cf_metrics)
        except Exception:
            pass

        ttm_income   = _calc_ttm(q_fin, income_metrics) if q_fin is not None else {}
        ttm_cashflow = _calc_ttm(q_cf,  cf_metrics)     if q_cf  is not None else {}
        if 'Free Cash Flow' in ttm_cashflow and ttm_cashflow['Free Cash Flow'] is not None:
            fcf = ttm_cashflow['Free Cash Flow']

        quarterly = _quarterly_trend(q_fin) if q_fin is not None else []

        # TTM period label
        ttm_period = "TTM"
        try:
            if q_fin is not None and not q_fin.empty:
                lc = list(q_fin.columns)[0]
                ld = lc.date() if hasattr(lc, 'date') else lc
                ttm_period = f"TTM (s/d {str(ld)[:7]})"
        except Exception:
            pass

        return {
            "available":    True,
            "ticker":       ticker_symbol,
            "sector":       sector,
            "industry":     industry,
            "sector_label": bench["label"],
            "sector_benchmark": {
                "pe_range": f"{bench['pe'][0]}–{bench['pe'][1]}x",
                "pb_range": f"{bench['pb'][0]}–{bench['pb'][1]}x",
                "roe_min":  f">{bench['roe_min']}%",
            },
            "valuation": {
                "market_cap": mktcap, "enterprise_value": ev,
                "shares_outstanding": shares, "float_shares": floats,
                "pe_trailing": pe, "pe_forward": fwd_pe, "pb_ratio": pb,
                "verdict": _assess_valuation(pe, sector),
            },
            "profitability": {
                "roe_pct": roe, "net_margin_pct": net_margin,
                "gross_margin_pct": gross_m, "ebitda_margin_pct": ebitda_m,
            },
            "growth": {
                "revenue_yoy_pct": rev_g, "earnings_yoy_pct": earn_g,
                "verdict": _assess_growth(rev_g, earn_g),
            },
            "financial_health": {
                "de_ratio": de, "current_ratio": cr,
                "free_cash_flow": fcf,
                "verdict": _assess_health(de, cr, fcf),
            },
            "dividends": {"yield_pct": div_yield, "payout_ratio_pct": div_payout, "rate": div_rate},
            "income_statement":       income_stmt,
            "cash_flow":              cash_flow,
            "quarterly_income_stmt":  quarterly_income_stmt,
            "quarterly_cashflow":     quarterly_cashflow,
            "ttm_income":             ttm_income,
            "ttm_cashflow":           ttm_cashflow,
            "ttm_period":             ttm_period,
            "quarterly_net_income":   quarterly,
        }
    except Exception as e:
        return {"available": False, "error": str(e)[:120]}


async def fetch_fundamentals(stock_code: str) -> dict:
    """Async wrapper. Cache is handled at API level (Supabase)."""
    ticker = f"{stock_code.upper()}.JK"
    loop   = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _fetch_sync, ticker)
