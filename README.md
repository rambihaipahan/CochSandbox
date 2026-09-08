# Storage Utilization Dashboard

A small internal dashboard for the monthly storage capacity review across
**FlashStack / Pure Storage**, **Cisco HyperFlex**, and **Oracle ZFS /
SuperCluster**. It reads and writes directly to
`data/Storage_Capacity_Overview_and_forecast.xlsx` -- the same workbook the
team already uses -- so the spreadsheet stays the single source of truth:
open it in Excel any time to see (or hand-edit) exactly what the dashboard
shows, and anything entered through the dashboard is saved straight back
into it.

## What it shows

- **KPI tiles** per platform: total capacity, in-use, available, %
  utilization (with trend vs. the prior month), and an estimated "months
  until the capacity threshold is reached" based on the existing forecast
  growth rates.
- **Capacity vs. in-use chart**: actual (solid) vs. forecast (dashed) TB
  over a rolling window (18 months back, 24 months forward from the latest
  recorded month).
- **% utilization by site**: one line per site/array under the platform,
  plus a dashed threshold line at that platform's configured max-utilization
  guardrail.
- **Monthly detail table**: the same data as plain numbers, aggregate or
  broken out by site -- forecast rows are shown in italics.
- **Record / correct a monthly reading**: pick a platform, site and month,
  enter the actual in-use TB (and, optionally, a new total capacity if
  capacity was added that month). Saving writes the value into the
  matching row of the workbook and recalculates every forecast month after
  it.
- **Download workbook**: fetches the current `.xlsx` as-is, for emailing or
  archiving.

## How it maps onto the workbook

The uploaded workbook already contains one clean "rollup" sheet per
site/array (`Total Storage | Month | In Use | ... | Forecast | In-use % |
Forecast % | Max %`), fed by formulas from the big raw `Data Collection
Sheet`. `config/platform_map.json` declares which of those sheets (and,
where a sheet holds more than one stacked block -- `EQUINIX DC` holds five)
belong to which of the three dashboard platforms:

| Platform | Sites |
|---|---|
| FlashStack / Pure Storage | MacPark, Equinix |
| Cisco HyperFlex | Denver, Mechelen, Brisbane, Kuala Lumpur, Chengdu, Weybridge, Gothenburg |
| Oracle ZFS / SuperCluster | Equinix SuperCluster, Windchill Prod (Oracle RAC) |

That file is the place to fix a misclassification or add a new site later
-- no code changes needed. One entry (`Windchill Prod (Oracle RAC)`) is
flagged with a `_note` in that file because it's an assumption worth a
second pair of eyes: it's grouped with the Oracle blocks in the source
sheet, but it may actually be an Oracle RAC VM running on FlashStack rather
than genuine ZFS-appliance storage.

Two other things worth knowing about the source data:

- **`Oracle ZFS / SuperCluster` has no per-month growth assumption in the
  workbook** (the `Growth (%)` column is blank for that block), so its
  forecast holds flat rather than projecting a rate nobody configured. Fill
  in that column in `EQUINIX DC` (starting row 126) if you want it to
  project growth like the other platforms do.
- The workbook's own forecast formulas have a one-cell bug: the first
  forecast month after the last actual anchors on its own (blank) "In Use"
  cell, which Excel reads as zero, and every later month compounds off
  that zero. The dashboard reimplements the roll-up math itself (see
  "Design decisions" below) and anchors correctly on the last actual value
  instead, so this doesn't affect what you see here -- but it's worth
  knowing if you're comparing against the raw cached values in Excel.

## Design decisions

- **Backend**: Python + FastAPI (`app/`), serving both the JSON API and the
  static frontend on the same port.
- **Recalculation**: rather than shelling out to Excel/LibreOffice to
  recalculate the workbook on every save, the app reimplements the rollup
  math natively (`app/excel_store.py`, `app/analytics.py`): sum of in-use /
  sum of total capacity for %, and the same monthly-compounded-growth
  formula the workbook itself uses for the forecast chain
  (`forecast[n] = forecast[n-1] * (1 + growth^(1/12) - 1) + change[n]`).
  This keeps saves instant and dependency-free, at the cost of the
  workbook's *other*, untouched formulas only refreshing their cached
  values the next time someone opens it in Excel (which recalculates
  automatically) -- they're never in a wrong state, just not eagerly
  recomputed by this app.
- **Writes are surgical**: an edit only ever touches the specific cells for
  the month you changed plus the forecast cells after it, in the one sheet
  involved. Every other sheet, row, and formula in the workbook -- including
  the raw `Data Collection Sheet` -- is left byte-for-byte untouched.
- **Updates, not inserts**: every rollup sheet already has a pre-built
  monthly calendar running years into the future as forecast placeholders,
  so recording a month is always an update to an existing row. The app
  deliberately never inserts spreadsheet rows (which would shift row
  numbers and invalidate `config/platform_map.json`'s `header_row`
  offsets). If you ever need to record a month beyond a sheet's existing
  calendar, extend that sheet's calendar in Excel first.

## Running it

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python run.py
```

The dashboard listens on **0.0.0.0:4100** (`app/config.py`) -- open
`http://BEM-HPD-TST01.cochlear.com:4100/` once it's running there.

### Running as a persistent service on BEM-HPD-TST01

Pick whichever matches how the server is managed:

**systemd (if the host runs a Linux service manager):**

```ini
# /etc/systemd/system/storage-dashboard.service
[Unit]
Description=Storage Utilization Dashboard
After=network.target

[Service]
WorkingDirectory=/opt/storage-dashboard
ExecStart=/opt/storage-dashboard/.venv/bin/python run.py
Restart=on-failure
User=svc-dashboard

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now storage-dashboard
```

**Windows Server / NSSM (if it's a Windows box, as the `.cochlear.com`
naming suggests):**

```powershell
nssm install StorageDashboard "C:\storage-dashboard\.venv\Scripts\python.exe" "run.py"
nssm set StorageDashboard AppDirectory "C:\storage-dashboard"
nssm start StorageDashboard
```

Either way, make sure port 4100 is opened on the host firewall for whoever
needs to reach the dashboard.

### Backing up the workbook

The workbook is a live database now, not just a static file -- back up
`data/Storage_Capacity_Overview_and_forecast.xlsx` on whatever schedule you
already use for the team's shared drive (or point a scheduled copy/`git
commit` at it). The app never deletes data, but a periodic snapshot is
cheap insurance.

## Project layout

```
app/
  main.py          FastAPI app + HTTP endpoints
  excel_store.py   Reads/writes the workbook; forecast recompute logic
  analytics.py     KPI / summary calculations
  schema.py        Request validation
  config.py        Paths, host/port
config/
  platform_map.json   Which sheets/sites belong to which platform
data/
  Storage_Capacity_Overview_and_forecast.xlsx   The live workbook
static/
  index.html, app.js, styles.css   The dashboard UI (vanilla JS + Chart.js)
  vendor/chart.umd.js              Chart.js, vendored for an offline server
run.py             Entry point (uvicorn on 0.0.0.0:4100)
```

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/platforms` | Platform + site list |
| `GET /api/summary` | Latest-month KPIs per platform |
| `GET /api/series/platform?platform=` | Aggregated monthly series for a platform |
| `GET /api/series/site?platform=&site=` | Monthly series for one site |
| `POST /api/entries` | `{platform, site, month, in_use_tb, total_capacity_tb?}` -- record/correct a month |
| `GET /api/export` | Download the current workbook |
