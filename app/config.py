import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"
CONFIG_DIR = BASE_DIR / "config"

WORKBOOK_PATH = DATA_DIR / "Storage_Capacity_Overview_and_forecast.xlsx"
PLATFORM_MAP_PATH = CONFIG_DIR / "platform_map.json"

HOST = "0.0.0.0"
PORT = 4100


def load_platform_map() -> dict:
    with open(PLATFORM_MAP_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return raw["platforms"]
