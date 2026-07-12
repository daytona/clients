# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Optional

from daytona_api_client_async import CreateVolume, HotmountRegion, Region, VolumeMountTokenDto, VolumesApi, VolumeType
from daytona_api_client_async.exceptions import NotFoundException

from .._utils.otel_decorator import with_instrumentation
from ..common.volume import Volume


class AsyncVolumeService:
    """Service for managing Daytona Volumes. Can be used to list, get, create and delete Volumes."""

    def __init__(self, volumes_api: VolumesApi):
        self.__volumes_api = volumes_api

    async def list(self) -> list[Volume]:
        """List all Volumes.

        Returns:
            list[Volume]: List of all Volumes.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                volumes = await daytona.volume.list()
                for volume in volumes:
                    print(f"{volume.name} ({volume.id})")
            ```
        """
        return [Volume.from_dto(volume) for volume in await self.__volumes_api.list_volumes()]

    @with_instrumentation()
    async def get(
        self,
        name: str,
        create: bool = False,
        type: Optional[VolumeType] = None,  # pylint: disable=redefined-builtin
        region: Optional[str] = None,
    ) -> Volume:
        """Get a Volume by name.

        Args:
            name (str): Name of the Volume to get.
            create (bool): If True, create a new Volume if it doesn't exist.
            type (Optional[VolumeType]): Type of the Volume to create (only used if create is True).
            region (Optional[str]): Region to create the Volume in. Required for blockmount volumes
                (pins the Volume to that region); selects the deployment region for hotmount volumes.

        Returns:
            Volume: The Volume object.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                volume = await daytona.volume.get("test-volume-name", create=True)
                print(f"{volume.name} ({volume.id})")
            ```
        """
        try:
            return Volume.from_dto(await self.__volumes_api.get_volume_by_name(name))
        except NotFoundException as e:
            if create:
                return await self.create(name, type=type, region=region)
            raise e

    @with_instrumentation()
    async def create(
        self,
        name: str,
        type: Optional[VolumeType] = None,  # pylint: disable=redefined-builtin
        region: Optional[str] = None,
    ) -> Volume:
        """Create a new Volume.

        Args:
            name (str): Name of the Volume to create.
            type (Optional[VolumeType]): Type of the Volume. Defaults to legacy.
            region (Optional[str]): Region to create the Volume in. For blockmount volumes it selects
                the region-local store the Volume's data lives in — a performance/placement knob, not an
                attach restriction, so Sandboxes in any region can attach it (colocation is just faster);
                optional for blockmount, defaulting to the organization's default region (or the first
                region offering blockmount) when omitted. For hotmount volumes it selects the deployment
                region (defaults to an active region). Not allowed for legacy volumes. The Volume's region
                is fixed for its lifetime.

        Returns:
            Volume: The Volume object.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                volume = await daytona.volume.create("test-volume")
                print(f"{volume.name} ({volume.id}); state: {volume.state}")
            ```
        """
        return Volume.from_dto(
            await self.__volumes_api.create_volume(CreateVolume(name=name, type=type, region=region))
        )

    @with_instrumentation()
    async def list_hotmount_regions(self) -> list[HotmountRegion]:
        """List the hotmount regions available for volume creation.

        Returns:
            list[HotmountRegion]: The active hotmount regions (id, label, geo).

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                for region in await daytona.volume.list_hotmount_regions():
                    print(f"{region.region} - {region.label}")
            ```
        """
        return await self.__volumes_api.list_hotmount_regions()

    @with_instrumentation()
    async def list_blockmount_regions(self) -> list[Region]:
        """List the regions where blockmount Volumes can be created.

        A blockmount Volume's data lives in the region it is created in (a performance/placement knob —
        Sandboxes in any region can attach it, colocation is just faster). Only regions a superadmin has
        enabled for blockmount are returned.

        Returns:
            list[Region]: The regions that support blockmount volumes.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                for region in await daytona.volume.list_blockmount_regions():
                    print(f"{region.id} - {region.name}")
            ```
        """
        return await self.__volumes_api.list_blockmount_regions()

    @with_instrumentation()
    async def get_mount_token(self, volume: Volume) -> VolumeMountTokenDto:
        """Create a short-lived mount token for a hotmount Volume.

        The token, together with the returned region gateway/binaries endpoints, is used to
        bootstrap the hotmount agent (inside a Sandbox or on customer infrastructure) and mount
        the Volume on the fly. Only hotmount volumes support this.

        Args:
            volume (Volume): The hotmount Volume to obtain a mount token for.

        Returns:
            VolumeMountTokenDto: The mount token, region endpoints, and expiration.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                volume = await daytona.volume.get("shared-fs")
                token = await daytona.volume.get_mount_token(volume)
            ```
        """
        return await self.__volumes_api.create_volume_mount_token(volume.id)

    @with_instrumentation()
    async def delete(self, volume: Volume) -> None:
        """Delete a Volume.

        Args:
            volume (Volume): Volume to delete.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                volume = await daytona.volume.get("test-volume")
                await daytona.volume.delete(volume)
                print("Volume deleted")
            ```
        """
        await self.__volumes_api.delete_volume(volume.id)
