"""Tests for core.webhook_action.fetch_tile_data — the read-side counterpart
to run_webhook_action, added so an Ithaca tile can be backed by a REST API
GET (data_source.type == "http") instead of only a SQL connection."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.webhook_action as webhook_action
from core.database import WebhookTarget
from core.webhook_action import WebhookActionError, fetch_tile_data


def _make_target(target_id, url="http://eln.example/api/reactions", **kwargs):
    # Write through webhook_action's own SessionLocal reference (bound at its
    # import time), not a fresh `from core.database import SessionLocal` —
    # another test module reassigning core.database.SessionLocal later in the
    # session would otherwise leave this write and fetch_tile_data's read
    # pointed at two different engines.
    db = webhook_action.SessionLocal()
    try:
        db.merge(WebhookTarget(id=target_id, label=target_id, url=url, **kwargs))
        db.commit()
    finally:
        db.close()


def _make_response(json_data, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.is_success = 200 <= status < 300
    resp.json.return_value = json_data
    resp.text = json.dumps(json_data)
    return resp


def _mock_client(resp):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=resp)
    return client


@pytest.mark.asyncio
async def test_fetch_tile_data_plain_array_response():
    _make_target("t_plain_array")
    resp = _make_response([{"id": 1, "name": "Rxn A"}, {"id": 2, "name": "Rxn B"}])
    with patch("httpx.AsyncClient", return_value=_mock_client(resp)):
        result = await fetch_tile_data("t_plain_array")
    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [[1, "Rxn A"], [2, "Rxn B"]]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_fetch_tile_data_json_path_extraction():
    _make_target("t_nested")
    payload = {"data": {"reactions": [{"id": 1, "status": "Complete"}]}}
    resp = _make_response(payload)
    with patch("httpx.AsyncClient", return_value=_mock_client(resp)):
        result = await fetch_tile_data("t_nested", json_path="data.reactions")
    assert result["columns"] == ["id", "status"]
    assert result["rows"] == [[1, "Complete"]]


@pytest.mark.asyncio
async def test_fetch_tile_data_row_limit_truncates():
    _make_target("t_many_rows")
    rows = [{"id": i} for i in range(10)]
    resp = _make_response(rows)
    with patch("httpx.AsyncClient", return_value=_mock_client(resp)):
        result = await fetch_tile_data("t_many_rows", row_limit=3)
    assert len(result["rows"]) == 3
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_fetch_tile_data_path_appended_to_target_url():
    _make_target("t_path", url="http://eln.example")
    resp = _make_response([])
    client = _mock_client(resp)
    with patch("httpx.AsyncClient", return_value=client):
        await fetch_tile_data("t_path", path="/api/dashboards/reactions")
    called_url = client.get.call_args.args[0]
    assert called_url == "http://eln.example/api/dashboards/reactions"


@pytest.mark.asyncio
async def test_fetch_tile_data_missing_json_path_raises():
    _make_target("t_bad_path")
    resp = _make_response({"other_key": []})
    with patch("httpx.AsyncClient", return_value=_mock_client(resp)):
        with pytest.raises(WebhookActionError, match="json_path"):
            await fetch_tile_data("t_bad_path", json_path="data.reactions")


@pytest.mark.asyncio
async def test_fetch_tile_data_non_array_response_raises():
    _make_target("t_not_array")
    resp = _make_response({"id": 1})
    with patch("httpx.AsyncClient", return_value=_mock_client(resp)):
        with pytest.raises(WebhookActionError, match="not a JSON array"):
            await fetch_tile_data("t_not_array")


@pytest.mark.asyncio
async def test_fetch_tile_data_http_error_status_raises():
    _make_target("t_error_status")
    resp = _make_response({"error": "nope"}, status=500)
    with patch("httpx.AsyncClient", return_value=_mock_client(resp)):
        with pytest.raises(WebhookActionError, match="500"):
            await fetch_tile_data("t_error_status")


@pytest.mark.asyncio
async def test_fetch_tile_data_missing_target_raises():
    with pytest.raises(WebhookActionError, match="No webhook endpoint"):
        await fetch_tile_data("t_does_not_exist")


@pytest.mark.asyncio
async def test_fetch_tile_data_always_gets_regardless_of_target_method():
    # Data sources never have a side effect — even a target configured for
    # action-button POSTs must be GET-only when used as a tile data source.
    _make_target("t_post_target", method="POST")
    resp = _make_response([])
    client = _mock_client(resp)
    with patch("httpx.AsyncClient", return_value=client):
        await fetch_tile_data("t_post_target")
    client.get.assert_called_once()
