---
title: IDX Stock Analyzer
emoji: 📈
colorFrom: blue
colorTo: indigo
sdk: docker
pinned: false
license: mit
---

# IDX Stock Analyzer API

AI-powered Indonesia stock (IDX/BEI) analysis backend.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/api/analyze` | Analyze a stock (cache-first) |
| GET | `/api/analysis/{code}` | Read cached analysis |
| GET | `/api/macro` | Macro data (IHSG, BI Rate, etc.) |
| GET | `/api/fundamental/{code}` | Fundamental data (P/E, income, TTM) |
| GET | `/api/status/{code}` | Cache status per stock |

## Usage

```bash
# Analyze BBCA (returns from cache if available, else runs AI)
curl -X POST https://michael123333-stock-analyzer.hf.space/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "BBCA"}'

# Get macro dashboard data
curl https://michael123333-stock-analyzer.hf.space/api/macro

# Check cache status
curl https://michael123333-stock-analyzer.hf.space/api/status/BBCA
```
