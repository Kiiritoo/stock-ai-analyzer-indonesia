import asyncio, json, sys, warnings
warnings.filterwarnings("ignore")
# Redirect stderr to suppress yfinance noise
import io
sys.stderr = io.StringIO()

async def test():
    from macro_fetcher import fetch_fed_rate, fetch_kurs_pajak, fetch_bi_rate, fetch_market_data
    fed, kmk, bi, mkt = await asyncio.gather(
        fetch_fed_rate(), fetch_kurs_pajak(), fetch_bi_rate(), fetch_market_data(),
    )
    sys.stderr = sys.__stderr__  # restore
    
    result = {
        "fed_rate":   {k: v for k, v in fed.items()},
        "kurs_pajak": {k: v for k, v in kmk.items()},
        "bi_rate":    {k: v for k, v in bi.items()},
        "market": {
            key: {"price": d.get("price"), "change_pct": d.get("change_pct"),
                  "available": d.get("available"), "unit": d.get("unit"),
                  "date": d.get("date"), "error": d.get("error", "")}
            for key, d in mkt.items()
        }
    }
    print(json.dumps(result, indent=2, default=str))

asyncio.run(test())
