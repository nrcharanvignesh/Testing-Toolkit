"""Tests for core.http_retry — retry logic with backoff and trace integration."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.http_retry import (
    MAX_RETRIES,
    MAX_WAIT,
    _RETRYABLE_STATUSES,
    request_with_retry,
    ssl_exception_types,
)


@pytest.fixture
def mock_client() -> MagicMock:
    return MagicMock(spec=httpx.AsyncClient)


class TestConstants:
    def test_max_retries_positive(self) -> None:
        assert MAX_RETRIES >= 1

    def test_retryable_statuses_are_frozenset(self) -> None:
        assert isinstance(_RETRYABLE_STATUSES, frozenset)
        assert 429 in _RETRYABLE_STATUSES
        assert 503 in _RETRYABLE_STATUSES

    def test_max_wait_bounded(self) -> None:
        assert MAX_WAIT > 0
        assert MAX_WAIT <= 120


class TestRequestWithRetry:
    @pytest.mark.asyncio
    async def test_returns_on_success(self, mock_client) -> None:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.headers = {}
        mock_client.request = AsyncMock(return_value=resp)

        with patch("core.trace.trace_dependency"):
            result = await request_with_retry(
                mock_client, "GET", "https://example.com/api"
            )
        assert result.status_code == 200
        assert mock_client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_429(self, mock_client) -> None:
        rate_limited = MagicMock(spec=httpx.Response)
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "0"}
        rate_limited.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "rate limited", request=MagicMock(), response=rate_limited
            )
        )

        success = MagicMock(spec=httpx.Response)
        success.status_code = 200
        success.headers = {}

        mock_client.request = AsyncMock(
            side_effect=[rate_limited, success]
        )

        with patch("core.trace.trace_dependency"), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await request_with_retry(
                mock_client, "GET", "https://example.com/api"
            )
        assert result.status_code == 200
        assert mock_client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self, mock_client) -> None:
        rate_limited = MagicMock(spec=httpx.Response)
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "0"}
        rate_limited.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "rate limited", request=MagicMock(), response=rate_limited
            )
        )

        mock_client.request = AsyncMock(return_value=rate_limited)

        with patch("core.trace.trace_dependency"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             pytest.raises(httpx.HTTPStatusError):
            await request_with_retry(
                mock_client, "POST", "https://example.com/api"
            )
        assert mock_client.request.call_count == MAX_RETRIES + 1


class TestSslExceptionTypes:
    def test_returns_tuple_of_types(self) -> None:
        types = ssl_exception_types()
        assert isinstance(types, tuple)
        assert len(types) >= 1
        for t in types:
            assert isinstance(t, type)
            assert issubclass(t, BaseException)
