from __future__ import annotations

import datetime as dt

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .analytics import platform_summary
from .config import STATIC_DIR, WORKBOOK_PATH
from .excel_store import get_store
from .schema import EntryIn

app = FastAPI(title="Storage Utilization Dashboard")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/platforms")
def platforms():
    return get_store().platforms()


@app.get("/api/series/site")
def series_for_site(platform: str, site: str):
    store = get_store()
    try:
        rows = store.site_series(platform, site)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"platform": platform, "site": site, "series": [r.to_dict() for r in rows]}


@app.get("/api/series/platform")
def series_for_platform(platform: str):
    store = get_store()
    if platform not in store.platform_map:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {platform}")
    agg = store.platform_series(platform)
    series = []
    for month in sorted(agg):
        b = agg[month]
        total = b["total_capacity_tb"] or 0.0
        in_use = b["in_use_tb"]
        forecast = b["forecast_tb"]
        series.append(
            {
                "month": month.isoformat(),
                "total_capacity_tb": round(total, 2),
                "in_use_tb": None if in_use is None else round(in_use, 2),
                "in_use_pct": None if (in_use is None or not total) else round((in_use / total) * 100, 2),
                "forecast_tb": None if forecast is None else round(forecast, 2),
                "forecast_pct": None if (forecast is None or not total) else round((forecast / total) * 100, 2),
                "is_actual": b["has_actual"] and in_use is not None,
            }
        )
    return {"platform": platform, "series": series}


@app.get("/api/summary")
def summary():
    store = get_store()
    return [platform_summary(store, p) for p in store.platform_map]


@app.post("/api/entries")
def add_entry(entry: EntryIn):
    store = get_store()
    if entry.platform not in store.platform_map:
        raise HTTPException(status_code=404, detail=f"Unknown platform: {entry.platform}")
    try:
        series = store.upsert_actual(
            platform=entry.platform,
            site=entry.site,
            month=entry.month,
            in_use_tb=entry.in_use_tb,
            total_capacity_tb=entry.total_capacity_tb,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"platform": entry.platform, "site": entry.site, "series": series}


@app.get("/api/export")
def export_workbook():
    return FileResponse(
        WORKBOOK_PATH,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=WORKBOOK_PATH.name,
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
