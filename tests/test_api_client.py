"""Tests for TmbApiClient request handling, using a fake aiohttp session."""
from __future__ import annotations

import aiohttp
import pytest

from custom_components.tmb.api import (
    TmbApiClient,
    TmbApiError,
    TmbAuthError,
    TmbRateLimitError,
    _parse_retry_after,
)


class _Resp:
    def __init__(self, status=200, payload=None, headers=None, text=""):
        self.status = status
        self._payload = payload
        self.headers = headers or {}
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, resp=None, exc=None):
        self._resp, self._exc, self.calls = resp, exc, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        if self._exc:
            raise self._exc
        return self._resp


def _client(session):
    return TmbApiClient(session, "id", "key")


async def test_credentials_and_timeout_are_sent():
    session = _Session(_Resp(payload={"features": []}))
    await _client(session).async_get_bus_lines()
    _, params, timeout = session.calls[0]
    assert params["app_id"] == "id" and params["app_key"] == "key"
    assert timeout is not None and timeout.total


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_errors(status):
    with pytest.raises(TmbAuthError):
        await _client(_Session(_Resp(status))).async_get_bus_lines()


async def test_rate_limit_carries_retry_after():
    session = _Session(_Resp(429, headers={"Retry-After": "90"}))
    with pytest.raises(TmbRateLimitError) as err:
        await _client(session).async_get_bus_lines()
    assert err.value.retry_after == 90


async def test_http_error_and_timeout_and_transport_error():
    with pytest.raises(TmbApiError):
        await _client(_Session(_Resp(500, text="boom"))).async_get_bus_lines()
    with pytest.raises(TmbApiError):
        await _client(_Session(exc=TimeoutError())).async_get_bus_lines()
    with pytest.raises(TmbApiError):
        await _client(_Session(exc=aiohttp.ClientConnectionError())).async_get_bus_lines()


async def test_rate_limit_is_an_api_error_subclass():
    assert issubclass(TmbRateLimitError, TmbApiError)


async def test_metro_alerts_are_parsed():
    payload = [{"entities": [{"line_name": "L1"}], "categories": {"effect_type": "TRAFFIC"}}]
    alerts = await _client(_Session(_Resp(payload=payload))).async_get_metro_alerts()
    assert alerts[0]["lines"] == ["L1"]


@pytest.mark.parametrize(
    ("value", "expected"), [(None, None), ("30", 30), (" 5 ", 5), ("0", None), ("x", None)]
)
def test_parse_retry_after(value, expected):
    assert _parse_retry_after(value) == expected
