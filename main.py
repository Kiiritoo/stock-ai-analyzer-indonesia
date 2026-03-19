from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import asyncio
import os

from news_fetcher    import fetch_articles, STOCK_COMPANY_MAP
from analyzer        import analyze_with_ollama
from price_fetcher   import fetch_price_data
from macro_fetcher   import fetch_all_macro, build_macro_context
from macro_analyzer  import analyze_macro

app = FastAPI(title="IDX Stock Analyzer", version="3.0.0")

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(static_dir, "index.html"))


# ── Stock analysis ─────────────────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    stock_code: str


@app.post("/api/analyze")
async def analyze_stock(req: AnalyzeRequest):
    code = req.stock_code.upper().strip()
    if not code or len(code) < 2 or len(code) > 6:
        raise HTTPException(status_code=400, detail="Kode saham tidak valid.")

    company_names = STOCK_COMPANY_MAP.get(code, [])
    company_label = company_names[0] if company_names else code

    # Fetch berita, harga, dan makro secara parallel
    try:
        (display_articles, ai_articles), price, macro = await asyncio.gather(
            fetch_articles(code, max_display=40, max_ai=15),
            fetch_price_data(code),
            fetch_all_macro(),               # pakai cache — sangat ringan
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil data: {e}")

    # Injeksi konteks makro ke analisis AI
    macro_ctx = build_macro_context(macro, code)

    try:
        analysis = await analyze_with_ollama(
            code, company_names, ai_articles, price, macro_context=macro_ctx
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {
        "stock_code":       code,
        "company_name":     company_label,
        "article_count":    len(display_articles),
        "ai_article_count": len(ai_articles),
        "articles":         display_articles,
        "price_data":       price,
        "analysis":         analysis,
        "macro_snapshot": {
            "bi_rate":   macro.get("bi_rate", {}).get("rate"),
            "fed_rate":  macro.get("fed_rate", {}).get("rate"),
            "usd_idr":   macro.get("market", {}).get("USD_IDR", {}).get("price"),
            "ihsg_chg":  macro.get("market", {}).get("IHSG", {}).get("change_pct"),
        },
    }


# ── Macro dashboard ────────────────────────────────────────────────────────────
@app.get("/api/macro")
async def get_macro():
    """Data pasar real-time (cache 5 menit). Ringan, aman di-poll setiap 5 menit."""
    try:
        data = await fetch_all_macro()
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ihsg-chart")
async def get_ihsg_chart():
    """IHSG 1-bulan data untuk mini chart (cache 5 menit)."""
    from macro_fetcher import fetch_ihsg_chart
    try:
        data = await fetch_ihsg_chart()
        return {"chart": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/macro-analysis")
async def get_macro_analysis():
    """Analisis sektor AI berdasarkan kondisi makro (cache 30 menit)."""
    try:
        macro = await fetch_all_macro()
        analysis = await analyze_macro(macro)
        return {"analysis": analysis, "macro": macro}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


# ── Utils ──────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "3.0.0"}


@app.get("/api/stocks")
async def list_stocks():
    return {
        "stocks": [
            {"code": code, "name": names[0]}
            for code, names in STOCK_COMPANY_MAP.items()
        ]
    }


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
