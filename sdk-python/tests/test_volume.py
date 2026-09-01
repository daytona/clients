# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daytona.common.errors import DaytonaNotFoundError, DaytonaRateLimitError
from daytona.common.volume import Volume
from daytona_api_client import VolumeDto


def _make_rate_limited_response():
    """Minimal stand-in for the urllib3/aiohttp response an ApiException wraps."""
    response = MagicMock()
    response.status = 429
    response.reason = "Too Many Requests"
    response.data = b'{"statusCode":429,"message":"ThrottlerException: Too Many Requests"}'
    response.headers = {
        "Retry-After": "10",
        "X-RateLimit-Limit-failed-auth": "20",
        "X-RateLimit-Remaining-failed-auth": "0",
    }
    return response


def _make_volume_dto(name="test-vol", vol_id="vol-123"):
    return VolumeDto(
        id=vol_id,
        name=name,
        organization_id="org-1",
        state="ready",
        error_reason=None,
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
        last_used_at="2025-01-01T00:00:00Z",
    )


def _make_volume(name="test-vol", vol_id="vol-123"):
    return Volume(
        id=vol_id,
        name=name,
        organization_id="org-1",
        state="ready",
        error_reason=None,
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
        last_used_at="2025-01-01T00:00:00Z",
    )


class TestSyncVolumeService:
    def _make_service(self):
        from daytona._sync.volume import VolumeService

        mock_api = MagicMock()
        return VolumeService(mock_api), mock_api

    def test_list(self):
        service, api = self._make_service()
        api.list_volumes.return_value = [_make_volume_dto()]
        result = service.list()
        assert len(result) == 1
        assert isinstance(result[0], Volume)

    def test_get(self):
        service, api = self._make_service()
        api.get_volume_by_name.return_value = _make_volume_dto()
        result = service.get("test-vol")
        assert isinstance(result, Volume)

    def test_get_with_create(self):
        from daytona_api_client.exceptions import NotFoundException

        service, api = self._make_service()
        api.get_volume_by_name.side_effect = NotFoundException(status=404, reason="Not found")
        api.create_volume.return_value = _make_volume_dto(name="new-vol")
        result = service.get("new-vol", create=True)
        assert isinstance(result, Volume)
        api.create_volume.assert_called_once()

    def test_get_not_found_raises(self):
        from daytona_api_client.exceptions import NotFoundException

        service, api = self._make_service()
        api.get_volume_by_name.side_effect = NotFoundException(status=404, reason="Not found")
        with pytest.raises(DaytonaNotFoundError):
            service.get("nonexistent")

    @pytest.mark.parametrize(
        ("method", "api_method", "call"),
        [
            ("list", "list_volumes", lambda service: service.list()),
            ("get", "get_volume_by_name", lambda service: service.get("test-vol")),
            ("create", "create_volume", lambda service: service.create("test-vol")),
            ("delete", "delete_volume", lambda service: service.delete(_make_volume())),
        ],
    )
    def test_rate_limit_raises_typed_error(self, method, api_method, call):
        from daytona_api_client.exceptions import ApiException

        service, api = self._make_service()
        getattr(api, api_method).side_effect = ApiException(
            status=429, reason="Too Many Requests", http_resp=_make_rate_limited_response()
        )

        with pytest.raises(DaytonaRateLimitError) as exc_info:
            call(service)

        assert exc_info.value.status_code == 429
        assert exc_info.value.headers["Retry-After"] == "10"

    def test_get_with_create_failure_is_prefixed_once(self):
        from daytona_api_client.exceptions import ApiException, NotFoundException

        service, api = self._make_service()
        api.get_volume_by_name.side_effect = NotFoundException(status=404, reason="Not found")
        api.create_volume.side_effect = ApiException(
            status=429, reason="Too Many Requests", http_resp=_make_rate_limited_response()
        )

        with pytest.raises(DaytonaRateLimitError) as exc_info:
            service.get("new-vol", create=True)

        assert str(exc_info.value).startswith("Failed to create volume: ")
        assert "Failed to get volume" not in str(exc_info.value)

    def test_create(self):
        service, api = self._make_service()
        api.create_volume.return_value = _make_volume_dto(name="new-vol")
        result = service.create("new-vol")
        assert isinstance(result, Volume)

    def test_delete(self):
        service, api = self._make_service()
        api.delete_volume.return_value = None
        vol = _make_volume()
        service.delete(vol)
        api.delete_volume.assert_called_once_with("vol-123")


class TestAsyncVolumeService:
    def _make_service(self):
        from daytona._async.volume import AsyncVolumeService

        mock_api = AsyncMock()
        return AsyncVolumeService(mock_api), mock_api

    @pytest.mark.asyncio
    async def test_list(self):
        service, api = self._make_service()
        api.list_volumes.return_value = [_make_volume_dto()]
        result = await service.list()
        assert len(result) == 1
        assert isinstance(result[0], Volume)

    @pytest.mark.asyncio
    async def test_get(self):
        service, api = self._make_service()
        api.get_volume_by_name.return_value = _make_volume_dto()
        result = await service.get("test-vol")
        assert isinstance(result, Volume)

    @pytest.mark.asyncio
    async def test_create(self):
        service, api = self._make_service()
        api.create_volume.return_value = _make_volume_dto(name="new-vol")
        result = await service.create("new-vol")
        assert isinstance(result, Volume)

    @pytest.mark.asyncio
    async def test_delete(self):
        service, api = self._make_service()
        vol = _make_volume()
        await service.delete(vol)
        api.delete_volume.assert_called_once_with("vol-123")

    @pytest.mark.asyncio
    async def test_get_with_create(self):
        from daytona_api_client_async.exceptions import NotFoundException

        service, api = self._make_service()
        api.get_volume_by_name.side_effect = NotFoundException(status=404, reason="Not found")
        api.create_volume.return_value = _make_volume_dto(name="new-vol")

        result = await service.get("new-vol", create=True)

        assert isinstance(result, Volume)
        api.create_volume.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_not_found_raises(self):
        from daytona_api_client_async.exceptions import NotFoundException

        service, api = self._make_service()
        api.get_volume_by_name.side_effect = NotFoundException(status=404, reason="Not found")
        with pytest.raises(DaytonaNotFoundError):
            await service.get("nonexistent")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "api_method", "call"),
        [
            ("list", "list_volumes", lambda service: service.list()),
            ("get", "get_volume_by_name", lambda service: service.get("test-vol")),
            ("create", "create_volume", lambda service: service.create("test-vol")),
            ("delete", "delete_volume", lambda service: service.delete(_make_volume())),
        ],
    )
    async def test_rate_limit_raises_typed_error(self, method, api_method, call):
        from daytona_api_client_async.exceptions import ApiException

        service, api = self._make_service()
        getattr(api, api_method).side_effect = ApiException(
            status=429, reason="Too Many Requests", http_resp=_make_rate_limited_response()
        )

        with pytest.raises(DaytonaRateLimitError) as exc_info:
            await call(service)

        assert exc_info.value.status_code == 429
        assert exc_info.value.headers["Retry-After"] == "10"

    @pytest.mark.asyncio
    async def test_get_with_create_failure_is_prefixed_once(self):
        from daytona_api_client_async.exceptions import ApiException, NotFoundException

        service, api = self._make_service()
        api.get_volume_by_name.side_effect = NotFoundException(status=404, reason="Not found")
        api.create_volume.side_effect = ApiException(
            status=429, reason="Too Many Requests", http_resp=_make_rate_limited_response()
        )

        with pytest.raises(DaytonaRateLimitError) as exc_info:
            await service.get("new-vol", create=True)

        assert str(exc_info.value).startswith("Failed to create volume: ")
        assert "Failed to get volume" not in str(exc_info.value)
