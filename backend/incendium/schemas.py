"""Request validation for user-controlled parameters."""
from __future__ import annotations

import re
from datetime import date as Date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .geo import region_ids, validate_bbox

CELL_RE = re.compile(r"^[a-z_]{2,32}:-?\d{1,2}\.\d{3},-?\d{1,3}\.\d{3}$")


class _Q(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _region(v: str) -> str:
    if v not in region_ids():
        raise ValueError(f"unknown region '{v}'")
    return v


class HotspotQuery(_Q):
    hours: int = Field(48, ge=1, le=720)
    since: datetime | None = None
    until: datetime | None = None
    bbox: str | None = Field(None, max_length=80)
    min_confidence: Literal["low", "nominal", "high"] | None = None
    satellite: Literal["NOAA-20", "NOAA-21"] | None = None
    state: str | None = Field(None, max_length=64)
    region: str | None = None
    limit: int = Field(20000, ge=1, le=30000)

    @field_validator("bbox")
    @classmethod
    def _bbox(cls, v: str | None) -> str | None:
        if v is None:
            return v
        parts = v.split(",")
        if len(parts) != 4:
            raise ValueError("bbox must be west,south,east,north")
        validate_bbox(tuple(float(p) for p in parts))  # type: ignore[arg-type]
        return v

    @field_validator("region")
    @classmethod
    def _r(cls, v: str | None) -> str | None:
        return None if v is None else _region(v)

    def bbox_tuple(self) -> tuple[float, float, float, float] | None:
        return None if self.bbox is None else tuple(float(p) for p in self.bbox.split(","))  # type: ignore[return-value]


class RegionQuery(_Q):
    region: str = "karnataka"
    date: Date | None = None

    @field_validator("region")
    @classmethod
    def _r(cls, v: str) -> str:
        return _region(v)


class ValidationQuery(_Q):
    region: str = "karnataka"
    season: str | None = Field(default=None, pattern=r"^\d{4}$")

    @field_validator("region")
    @classmethod
    def _r(cls, v: str) -> str:
        return _region(v)


class CellQuery(_Q):
    cell_id: str = Field(..., max_length=64)
    date: Date | None = None

    @field_validator("cell_id")
    @classmethod
    def _c(cls, v: str) -> str:
        if not CELL_RE.match(v):
            raise ValueError("malformed cell_id")
        return v


class WeatherQuery(_Q):
    region: str = "karnataka"
    hour_offset: int = Field(0, ge=-48, le=48)

    @field_validator("region")
    @classmethod
    def _r(cls, v: str) -> str:
        return _region(v)
