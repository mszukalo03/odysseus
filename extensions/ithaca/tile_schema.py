"""
extensions/ithaca/tile_schema.py

The portable tile config contract. A tile is a small, self-contained JSON
document describing where its data comes from and how to visualize it — it
never embeds credentials, only a `connection_ref` name resolved locally
against this instance's `ExternalDbConnection` rows (core/database.py) at
query time. That's what lets a tile config be zipped/downloaded and imported
on another Odysseus instance (extensions/ithaca/tile_packaging.py, Phase 4)
without leaking secrets.

Nothing here is specific to any one dataset (e.g. the app_config pilot table)
— `data_source.query` is arbitrary SQL against whatever connection it names.

`tile_schema.json` (same directory) is a JSON Schema mirror of this model,
shipped inside exported tile packages so they validate without importing
Python — the same self-describing-manifest idea as extension.json.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = 1

VizType = Literal["table", "stat", "bar", "line", "pie", "list"]

_ID_RE = re.compile(r"^[a-z0-9_\-]+$")


class TileColumn(BaseModel):
    field: str
    label: str = ""


class TileDataSource(BaseModel):
    type: Literal["postgres"] = "postgres"
    connection_ref: str
    query: str


class TileViz(BaseModel):
    type: VizType = "table"
    columns: list[TileColumn] = Field(default_factory=list)
    # For bar/line/pie: which result columns map to the category label and
    # the numeric value. Ignored by table/list/stat.
    label_field: Optional[str] = None
    value_field: Optional[str] = None


class TileConfig(BaseModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    title: str
    slot: Optional[str] = None
    data_source: TileDataSource
    viz: TileViz = Field(default_factory=TileViz)
    refresh_interval_seconds: int = 900
    created_by: str = "manual"
    notes: str = ""

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not _ID_RE.match(v or ""):
            raise ValueError("id must match [a-z0-9_-]+")
        return v

    @field_validator("refresh_interval_seconds")
    @classmethod
    def _min_refresh(cls, v: int) -> int:
        # A misconfigured tile (or a bad AI proposal) must not be able to
        # hammer an external DB every few seconds.
        return max(60, int(v))
