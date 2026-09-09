import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = BASE_DIR / "config"

# Points at the repo's own bundled copy by default. Set DASHBOARD_WORKBOOK_PATH
# to point this at a OneDrive/SharePoint-synced local copy instead, so the
# dashboard reads/writes the team's live file rather than a static snapshot --
# see README.md "Pointing the dashboard at the SharePoint-hosted workbook".
WORKBOOK_PATH = Path(
    os.environ.get(
        "DASHBOARD_WORKBOOK_PATH",
        DATA_DIR / "Storage_Capacity_Overview_and_forecast.xlsx",
    )
)
PLATFORM_MAP_PATH = CONFIG_DIR / "platform_map.json"

HOST = "0.0.0.0"
PORT = 4100


def load_platform_map() -> dict:
    with open(PLATFORM_MAP_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return raw["platforms"]
