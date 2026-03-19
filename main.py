from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import asyncio
import os

from news_fetcher import fetch_articles, get_company_keywords, STOCK_COMPANY_MAP
from analyzer import analyze_with_ollama
from price_fetcher import fetch_price_data

app = FastAPI(title="IDX Stock Analyzer", version="2.0.0")

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def root():
    return FileResponse(os.path.join(static_dir, "index.html"))


class AnalyzeRequest(BaseModel):
    stock_code: str


@app.post("/api/analyze")
async def analyze_stock(req: AnalyzeRequest):
    code = req.stock_code.upper().strip()
    if not code or len(code) < 2 or len(code) > 6:
        raise HTTPException(status_code=400, detail="Kode saham tidak valid.")

    company_names = STOCK_COMPANY_MAP.get(code, [])
    company_label = company_names[0] if company_names else code

    # Fetch berita + harga secara parallel (lebih cepat)
    try:
        articles_task        = fetch_articles(code, max_display=40, max_ai=15)
        price_task           = fetch_price_data(code)
        (display_articles, ai_articles), price = await asyncio.gather(articles_task, price_task)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gagal mengambil data: {str(e)}")

    # Analisis AI: hanya kirim top-relevant articles supaya prompt tidak membengkak
    try:
        analysis = await analyze_with_ollama(code, company_names, ai_articles, price)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {
        "stock_code":       code,
        "company_name":     company_label,
        "article_count":    len(display_articles),   # total ditampilkan di UI
        "ai_article_count": len(ai_articles),         # yang dikirim ke AI
        "articles":         display_articles,          # semua artikel untuk UI
        "price_data":       price,
        "analysis":         analysis,
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


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
