# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daytona.common.warm_pool import WarmPool
from daytona_api_client import WarmPool as WarmPoolDto


def _make_warm_pool_dto(pool_id="wp-123", pool=3):
    return WarmPoolDto(
        id=pool_id,
        organization_id="org-1",
        snapshot="test-snapshot",
        target="us",
        pool=pool,
        current_size=1,
        cpu=2,
        mem=4,
        disk=10,
        os_user="daytona",
        env={},
        error_reason=None,
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
    )


class TestSyncWarmPoolService:
    def _make_service(self):
        from daytona._sync.warm_pool import WarmPoolService

        mock_api = MagicMock()
        return WarmPoolService(mock_api), mock_api

    def test_list(self):
        service, api = self._make_service()
        api.list_warm_pools.return_value = [_make_warm_pool_dto()]
        result = service.list()
        assert len(result) == 1
        assert isinstance(result[0], WarmPool)

    def test_create(self):
        service, api = self._make_service()
        api.create_warm_pool.return_value = _make_warm_pool_dto(pool=5)
        result = service.create("test-snapshot", pool=5)
        assert isinstance(result, WarmPool)
        create_dto = api.create_warm_pool.call_args.args[0]
        assert create_dto.snapshot == "test-snapshot"
        assert create_dto.pool == 5
        assert create_dto.target is None

    def test_update(self):
        service, api = self._make_service()
        api.update_warm_pool.return_value = _make_warm_pool_dto(pool=10)
        result = service.update("wp-123", pool=10)
        assert isinstance(result, WarmPool)
        update_args = api.update_warm_pool.call_args.args
        assert update_args[0] == "wp-123"
        assert update_args[1].pool == 10

    def test_delete(self):
        service, api = self._make_service()
        api.delete_warm_pool.return_value = None
        service.delete("wp-123")
        api.delete_warm_pool.assert_called_once_with("wp-123")


class TestAsyncWarmPoolService:
    def _make_service(self):
        from daytona._async.warm_pool import AsyncWarmPoolService

        mock_api = AsyncMock()
        return AsyncWarmPoolService(mock_api), mock_api

    @pytest.mark.asyncio
    async def test_list(self):
        service, api = self._make_service()
        api.list_warm_pools.return_value = [_make_warm_pool_dto()]
        result = await service.list()
        assert len(result) == 1
        assert isinstance(result[0], WarmPool)

    @pytest.mark.asyncio
    async def test_create(self):
        service, api = self._make_service()
        api.create_warm_pool.return_value = _make_warm_pool_dto(pool=5)
        result = await service.create("test-snapshot", pool=5, target="us")
        assert isinstance(result, WarmPool)
        create_dto = api.create_warm_pool.call_args.args[0]
        assert create_dto.target == "us"

    @pytest.mark.asyncio
    async def test_update(self):
        service, api = self._make_service()
        api.update_warm_pool.return_value = _make_warm_pool_dto(pool=0)
        result = await service.update("wp-123", pool=0)
        assert isinstance(result, WarmPool)

    @pytest.mark.asyncio
    async def test_delete(self):
        service, api = self._make_service()
        await service.delete("wp-123")
        api.delete_warm_pool.assert_called_once_with("wp-123")
