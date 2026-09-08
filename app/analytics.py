"""Derived KPIs for the dashboard's summary cards. All math here is plain
Python re-implementing the workbook's own roll-up formulas (sum of in-use /
sum of total capacity, compounded monthly growth for forecast months) rather
than depending on Excel to have recalculated the cached formula results."""
from __future__ import annotations

import datetime as dt

from .excel_store import ExcelStore


def _min_max_pct(store: ExcelStore, platform: str) -> float | None:
    thresholds = []
    for site_cfg in store.platform_map[platform]["sites"]:
        rows = store.site_series(platform, site_cfg["site"])
        if rows and rows[-1].max_pct is not None:
            thresholds.append(rows[-1].max_pct)
    return min(thresholds) if thresholds else None


def platform_summary(store: ExcelStore, platform: str) -> dict:
    agg = store.platform_series(platform)
    months = sorted(agg.keys())

    actual_months = [m for m in months if agg[m]["has_actual"] and agg[m]["in_use_tb"] is not None]
    latest = actual_months[-1] if actual_months else None
    prior = actual_months[-2] if len(actual_months) > 1 else None

    latest_bucket = agg[latest] if latest else None
    prior_bucket = agg[prior] if prior else None

    latest_pct = None
    latest_total = None
    latest_in_use = None
    trend_pct_points = None
    if latest_bucket:
        latest_total = round(latest_bucket["total_capacity_tb"], 2)
        latest_in_use = round(latest_bucket["in_use_tb"], 2)
        latest_pct = round((latest_in_use / latest_total) * 100, 2) if latest_total else None
        if prior_bucket and prior_bucket["total_capacity_tb"]:
            prior_pct = (prior_bucket["in_use_tb"] / prior_bucket["total_capacity_tb"]) * 100
            trend_pct_points = round(latest_pct - prior_pct, 2) if latest_pct is not None else None

    threshold = _min_max_pct(store, platform)

    months_to_threshold = None
    if threshold is not None and latest is not None:
        future_months = [m for m in months if m > latest]
        for m in future_months:
            bucket = agg[m]
            if not bucket["total_capacity_tb"]:
                continue
            forecast_pct = (bucket["forecast_tb"] / bucket["total_capacity_tb"]) * 100
            if forecast_pct >= threshold:
                months_to_threshold = (m.year - latest.year) * 12 + (m.month - latest.month)
                break

    return {
        "platform": platform,
        "latest_month": latest.isoformat() if latest else None,
        "total_capacity_tb": latest_total,
        "in_use_tb": latest_in_use,
        "available_tb": (round(latest_total - latest_in_use, 2) if latest_total and latest_in_use is not None else None),
        "in_use_pct": latest_pct,
        "trend_pct_points": trend_pct_points,
        "max_pct_threshold": threshold,
        "months_to_threshold": months_to_threshold,
    }
