import datetime as dt

from pydantic import BaseModel, Field


class EntryIn(BaseModel):
    platform: str
    site: str
    month: dt.date = Field(description="Any day within the target month; day-of-month is ignored.")
    in_use_tb: float = Field(gt=0)
    total_capacity_tb: float | None = Field(default=None, gt=0)
