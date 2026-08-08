"""Guards extensions/ithaca/tile_schema.py's `data_source.type` literal and
its hand-maintained JSON Schema mirror (tile_schema.json) from drifting apart
— there's no generator, so the two must be kept in sync by hand on every
change, and this is a known drift risk (see the multi-dialect DB plan)."""

import json
import os
from typing import get_args

from extensions.ithaca.tile_schema import TileDataSource

_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "extensions", "ithaca", "tile_schema.json")


def _type_field_literal_values():
    annotation = TileDataSource.model_fields["type"].annotation
    return set(get_args(annotation))


def test_data_source_type_literal_matches_json_schema_enum():
    with open(_JSON_PATH) as f:
        schema = json.load(f)
    json_enum = set(schema["properties"]["data_source"]["properties"]["type"]["enum"])
    assert json_enum == _type_field_literal_values()


def test_data_source_type_includes_all_dialect_kinds():
    from core.db_dialects import SUPPORTED_KINDS
    assert _type_field_literal_values() == set(SUPPORTED_KINDS)
