"""
Reads and writes the monthly storage-utilization rollup blocks inside the
Storage_Capacity_Overview_and_forecast.xlsx workbook.

Design notes (see README.md for the full rationale):
  * Every "site" the dashboard knows about is declared in config/platform_map.json
    as a (sheet, header_row) pair. header_row is the row holding that block's own
    "Total Storage / Month / In Use / ..." header labels -- some sheets (e.g.
    "EQUINIX DC") stack several independent blocks on top of each other, so the
    header row is what disambiguates them.
  * Column layout inside every block is fixed:
      A label | B Total Storage (TB) | C Month | D In Use (TB) | E Change (TB)
      | F Growth factor (annual, e.g. 1.08 = +8%/yr) | G Forecast (TB)
      | H In-use % | I Forecast % | J Max % threshold
  * Historical/cached numeric values are read once at startup from a
    data_only=True load (openpyxl cannot both preserve formulas and hand back
    their last-calculated value from a single load). All subsequent edits are
    applied as plain numbers -- per the project's chosen approach, this app
    recomputes the roll-up math itself rather than depending on Excel to
    recalculate the workbook -- through a second, formula-preserving handle
    that is what actually gets saved back to disk. This means only the exact
    cells the dashboard edits ever change; every other sheet/cell/formula in
    the workbook is left byte-for-byte alone.
  * The dashboard only ever UPDATES an existing month row (every rollup sheet
    already carries a pre-built monthly calendar stretching years into the
    future as forecast placeholders). It never inserts/removes spreadsheet
    rows, which would shift row numbers and silently invalidate the
    header_row offsets recorded in platform_map.json.
"""
from __future__ import annotations

import datetime as dt
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import openpyxl

from .config import WORKBOOK_PATH, load_platform_map

COL_LABEL = 1
COL_TOTAL = 2
COL_MONTH = 3
COL_IN_USE = 4
COL_CHANGE = 5
COL_GROWTH = 6
COL_FORECAST = 7
COL_IN_USE_PCT = 8
COL_FORECAST_PCT = 9
COL_MAX_PCT = 10

_MONTH_STR_FORMATS = ("%b-%y", "%b-%Y", "%B-%y", "%B-%Y")


def _parse_month(value) -> Optional[dt.date]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().replace(day=1)
    if isinstance(value, dt.date):
        return value.replace(day=1)
    if isinstance(value, str):
        s = value.strip().replace("'", "")
        for fmt in _MONTH_STR_FORMATS:
            try:
                return dt.datetime.strptime(s, fmt).date().replace(day=1)
            except ValueError:
                continue
        m = re.match(r"^([A-Za-z]{3,9})\s*(\d{2,4})$", s)
        if m:
            for fmt in ("%b %y", "%b %Y", "%B %y", "%B %Y"):
                try:
                    return dt.datetime.strptime(f"{m.group(1)} {m.group(2)}", fmt).date().replace(day=1)
                except ValueError:
                    continue
    return None


@dataclass
class RowRecord:
    row: int
    label: Optional[str]
    total_capacity_tb: Optional[float]
    month: dt.date
    in_use_tb: Optional[float]
    change_tb: Optional[float]
    growth_factor: Optional[float]
    forecast_tb: Optional[float]
    in_use_pct: Optional[float]
    forecast_pct: Optional[float]
    max_pct: Optional[float]

    @property
    def is_actual(self) -> bool:
        return self.in_use_tb is not None

    def to_dict(self) -> dict:
        return {
            "month": self.month.isoformat(),
            "total_capacity_tb": self.total_capacity_tb,
            "in_use_tb": self.in_use_tb,
            "available_tb": (
                None
                if self.total_capacity_tb is None or self.in_use_tb is None
                else round(self.total_capacity_tb - self.in_use_tb, 3)
            ),
            "in_use_pct": None if (not self.is_actual or self.in_use_pct is None) else round(self.in_use_pct, 2),
            "forecast_tb": None if self.forecast_tb is None else round(self.forecast_tb, 2),
            "forecast_pct": None if self.forecast_pct is None else round(self.forecast_pct, 2),
            "max_pct": self.max_pct,
            "is_actual": self.is_actual,
        }


@dataclass
class SiteBlock:
    platform: str
    site: str
    sheet: str
    header_row: int
    rows: dict = field(default_factory=dict)  # row number -> RowRecord, ordered by row number

    def sorted_rows(self) -> list[RowRecord]:
        return [self.rows[r] for r in sorted(self.rows)]


class WorkbookLockedError(Exception):
    """Raised when the workbook file can't be written because something else
    (typically Excel, or OneDrive mid-sync) has it open/locked on disk."""


class ExcelStore:
    def __init__(self, path=WORKBOOK_PATH):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.platform_map = load_platform_map()
        self.blocks: dict[tuple[str, str], SiteBlock] = {}
        self._mtime = None
        self._load()

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def _load(self):
        wb_values = openpyxl.load_workbook(self.path, data_only=True)
        for platform, meta in self.platform_map.items():
            for site_cfg in meta["sites"]:
                sheet = site_cfg["sheet"]
                header_row = site_cfg["header_row"]
                site = site_cfg["site"]
                ws = wb_values[sheet]
                block = self._parse_block(ws, header_row)
                sb = SiteBlock(platform=platform, site=site, sheet=sheet, header_row=header_row, rows=block)
                self.blocks[(platform, site)] = sb
                self._heal_forecast_chain(sb)
        wb_values.close()
        # Formula-preserving handle used only for targeted writes + save.
        self._wb = openpyxl.load_workbook(self.path, data_only=False)
        self._mtime = self.path.stat().st_mtime

    def _reload_if_changed(self):
        """Picks up edits made outside this app -- e.g. someone editing the
        workbook directly in Excel/SharePoint when it's pointed at a
        OneDrive-synced file, which writes to disk independently of this
        process. Cheap when nothing changed (just a stat() call)."""
        try:
            current_mtime = self.path.stat().st_mtime
        except OSError:
            return
        if current_mtime != self._mtime:
            with self._lock:
                current_mtime = self.path.stat().st_mtime
                if current_mtime != self._mtime:
                    self.blocks = {}
                    self._load()

    @staticmethod
    def _parse_block(ws, header_row: int) -> dict:
        end_row = ws.max_row
        for r in range(header_row + 1, ws.max_row + 1):
            if ws.cell(row=r, column=COL_MONTH).value == "Month":
                end_row = r - 1
                break
        rows = {}
        for r in range(header_row + 1, end_row + 1):
            month = _parse_month(ws.cell(row=r, column=COL_MONTH).value)
            if month is None:
                continue
            rows[r] = RowRecord(
                row=r,
                label=ws.cell(row=r, column=COL_LABEL).value,
                total_capacity_tb=ws.cell(row=r, column=COL_TOTAL).value,
                month=month,
                in_use_tb=ws.cell(row=r, column=COL_IN_USE).value,
                change_tb=ws.cell(row=r, column=COL_CHANGE).value,
                growth_factor=ws.cell(row=r, column=COL_GROWTH).value,
                forecast_tb=ws.cell(row=r, column=COL_FORECAST).value,
                in_use_pct=ws.cell(row=r, column=COL_IN_USE_PCT).value,
                forecast_pct=ws.cell(row=r, column=COL_FORECAST_PCT).value,
                max_pct=ws.cell(row=r, column=COL_MAX_PCT).value,
            )
        return rows

    @staticmethod
    def _heal_forecast_chain(block: "SiteBlock"):
        """The source workbook anchors each block's first forecast row on its
        own (blank) In Use cell (`=D58` where D58 is empty), which Excel reads
        as zero, and every later row compounds off that zero forever -- so the
        cached 'Forecast' column the file ships with is flatlined at 0 past
        the last recorded actual. Recompute that chain in memory right away,
        anchored on the last actual instead, so every read -- even before any
        edit is made through this app -- shows a real projection."""
        ordered = block.sorted_rows()
        last_actual_idx = -1
        for i, rec in enumerate(ordered):
            if rec.is_actual:
                last_actual_idx = i
        ExcelStore._recompute_forecast_chain(ordered, start_index=last_actual_idx + 1)

    # ------------------------------------------------------------------ #
    # Read API
    # ------------------------------------------------------------------ #
    def platforms(self) -> list[dict]:
        out = []
        for platform, meta in self.platform_map.items():
            sites = [cfg["site"] for cfg in meta["sites"]]
            out.append({"key": platform, "label": meta["label"], "sites": sites})
        return out

    def get_block(self, platform: str, site: str) -> SiteBlock:
        self._reload_if_changed()
        key = (platform, site)
        if key not in self.blocks:
            raise KeyError(f"Unknown platform/site combination: {platform}/{site}")
        return self.blocks[key]

    def site_series(self, platform: str, site: str) -> list[RowRecord]:
        return self.get_block(platform, site).sorted_rows()

    def platform_series(self, platform: str) -> dict[dt.date, dict]:
        """Aggregate every site under a platform into one monthly total series.
        A month only counts as an actual (has_actual) if every site that has a
        row for that month reports an actual in_use_tb -- otherwise the total
        would silently understate reality by treating a missing site as zero."""
        totals: dict[dt.date, dict] = {}
        for site_cfg in self.platform_map[platform]["sites"]:
            site = site_cfg["site"]
            for rec in self.site_series(platform, site):
                bucket = totals.setdefault(
                    rec.month,
                    {"total_capacity_tb": 0.0, "in_use_sum": 0.0, "forecast_tb": 0.0, "has_actual": True},
                )
                bucket["total_capacity_tb"] += rec.total_capacity_tb or 0.0
                if rec.in_use_tb is None:
                    bucket["has_actual"] = False
                else:
                    bucket["in_use_sum"] += rec.in_use_tb
                bucket["forecast_tb"] += (rec.forecast_tb if rec.forecast_tb is not None else (rec.in_use_tb or 0.0))
        for bucket in totals.values():
            bucket["in_use_tb"] = bucket["in_use_sum"] if bucket["has_actual"] else None
            del bucket["in_use_sum"]
        return totals

    # ------------------------------------------------------------------ #
    # Write API
    # ------------------------------------------------------------------ #
    def upsert_actual(
        self,
        platform: str,
        site: str,
        month: dt.date,
        in_use_tb: float,
        total_capacity_tb: Optional[float] = None,
    ) -> list[dict]:
        """Record (or correct) an actual monthly in-use figure and cascade the
        forecast recompute forward through the rest of the block. Returns the
        full updated series for that site as plain dicts."""
        month = month.replace(day=1)
        with self._lock:
            block = self.get_block(platform, site)
            ordered = block.sorted_rows()
            target = next((r for r in ordered if r.month == month), None)
            if target is None:
                raise ValueError(
                    f"{site} ({platform}) has no pre-built calendar row for {month.isoformat()}. "
                    "Extend the monthly calendar for this sheet in Excel first, then retry."
                )

            ws = self._wb[block.sheet]

            if total_capacity_tb is not None:
                target.total_capacity_tb = total_capacity_tb
                ws.cell(row=target.row, column=COL_TOTAL, value=total_capacity_tb)

            target.in_use_tb = in_use_tb
            target.in_use_pct = (
                None if not target.total_capacity_tb else (in_use_tb / target.total_capacity_tb) * 100
            )
            target.forecast_tb = in_use_tb
            target.forecast_pct = target.in_use_pct
            ws.cell(row=target.row, column=COL_IN_USE, value=in_use_tb)
            ws.cell(row=target.row, column=COL_IN_USE_PCT, value=target.in_use_pct)
            ws.cell(row=target.row, column=COL_FORECAST, value=target.forecast_tb)
            ws.cell(row=target.row, column=COL_FORECAST_PCT, value=target.forecast_pct)

            changed = self._recompute_forecast_chain(ordered, start_index=ordered.index(target) + 1)
            for rec in changed:
                ws.cell(row=rec.row, column=COL_FORECAST, value=rec.forecast_tb)
                ws.cell(row=rec.row, column=COL_FORECAST_PCT, value=rec.forecast_pct)
            self._save()
            return [r.to_dict() for r in ordered]

    @staticmethod
    def _recompute_forecast_chain(ordered: list[RowRecord], start_index: int) -> list[RowRecord]:
        """Recompute every still-forecast-only row from start_index onward,
        in memory only, chaining from the previous row's known/forecast value.
        Mirrors the workbook's own compounding formula:
        G[r] = prev * (1 + (annual_growth^(1/12) - 1)) + change[r].
        Returns the rows it actually changed, for callers that need to persist them."""
        prev_value = None
        if start_index > 0:
            prev_row = ordered[start_index - 1]
            prev_value = prev_row.in_use_tb if prev_row.is_actual else prev_row.forecast_tb

        changed = []
        for rec in ordered[start_index:]:
            if rec.is_actual:
                prev_value = rec.in_use_tb
                continue
            if prev_value is None:
                continue
            growth = rec.growth_factor if rec.growth_factor else 1.0
            monthly_factor = growth ** (1.0 / 12.0)
            change = rec.change_tb or 0.0
            new_forecast = prev_value * (1 + (monthly_factor - 1)) + change
            rec.forecast_tb = new_forecast
            rec.forecast_pct = None if not rec.total_capacity_tb else (new_forecast / rec.total_capacity_tb) * 100
            changed.append(rec)
            prev_value = new_forecast
        return changed

    def _save(self):
        try:
            self._wb.save(self.path)
        except PermissionError as exc:
            raise WorkbookLockedError(
                f"Couldn't save -- {self.path.name} is currently open/locked (e.g. someone has it "
                "open in Excel, or OneDrive is mid-sync). Close it and try again."
            ) from exc
        self._mtime = self.path.stat().st_mtime


_store: Optional[ExcelStore] = None
_store_lock = threading.Lock()


def get_store() -> ExcelStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ExcelStore()
    return _store
