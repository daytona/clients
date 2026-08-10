# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from daytona_api_client_async import CreateWarmPool, UpdateWarmPool, WarmPoolsApi

from .._utils.otel_decorator import with_instrumentation
from ..common.warm_pool import WarmPool


class AsyncWarmPoolService:
    """Service for managing Daytona Warm Pools. Can be used to list, create, update and delete Warm Pools."""

    def __init__(self, warm_pools_api: WarmPoolsApi):
        self.__warm_pools_api = warm_pools_api

    @with_instrumentation()
    async def list(self) -> list[WarmPool]:
        """List all Warm Pools in the organization.

        Returns:
            list[WarmPool]: List of all Warm Pools.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                pools = await daytona.warm_pool.list()
                for pool in pools:
                    print(f"{pool.snapshot}: {pool.current_size}/{pool.pool} ready")
            ```
        """
        return [WarmPool.from_dto(pool) for pool in await self.__warm_pools_api.list_warm_pools()]

    @with_instrumentation()
    async def create(self, snapshot: str, pool: int, target: str | None = None) -> WarmPool:
        """Create a new Warm Pool.

        Args:
            snapshot (str): The snapshot (ID or name) to keep warm sandboxes for.
            pool (int): Number of warm sandboxes to keep ready.
            target (str | None): Target region for the Warm Pool. Defaults to the
                organization default region.

        Returns:
            WarmPool: The newly created Warm Pool.

        Raises:
            ApiException: If a Warm Pool for the same snapshot and region already exists (409).

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                pool = await daytona.warm_pool.create("my-snapshot", pool=5)
                print(f"Created warm pool {pool.id} in {pool.target}")
            ```
        """
        return WarmPool.from_dto(
            await self.__warm_pools_api.create_warm_pool(CreateWarmPool(snapshot=snapshot, pool=pool, target=target))
        )

    @with_instrumentation()
    async def update(self, warm_pool_id: str, pool: int) -> WarmPool:
        """Update the desired size of a Warm Pool.

        Args:
            warm_pool_id (str): ID of the Warm Pool to update.
            pool (int): New desired number of warm sandboxes (0 drains the pool).

        Returns:
            WarmPool: The updated Warm Pool.

        Raises:
            NotFoundException: If the Warm Pool does not exist.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                pool = await daytona.warm_pool.update("warm-pool-id", pool=10)
            ```
        """
        return WarmPool.from_dto(await self.__warm_pools_api.update_warm_pool(warm_pool_id, UpdateWarmPool(pool=pool)))

    @with_instrumentation()
    async def delete(self, warm_pool_id: str) -> None:
        """Delete a Warm Pool.

        Args:
            warm_pool_id (str): ID of the Warm Pool to delete.

        Raises:
            NotFoundException: If the Warm Pool does not exist.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                await daytona.warm_pool.delete("warm-pool-id")
                print("Warm pool deleted")
            ```
        """
        await self.__warm_pools_api.delete_warm_pool(warm_pool_id)
