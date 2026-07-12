# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from daytona_api_client import SandboxVolume as ApiVolumeMount
from daytona_api_client import VolumeDto, VolumeMountTokenDto, VolumeType
from daytona_api_client_async import SandboxVolume as AsyncApiVolumeMount
from daytona_api_client_async import VolumeDto as AsyncVolumeDto
from daytona_api_client_async import VolumeMountTokenDto as AsyncVolumeMountTokenDto

__all__ = [
    "Volume",
    "VolumeMount",
    "VolumeMountTokenDto",
    "VolumePullResult",
    "VolumeType",
    "build_hotmount_mount_command",
]


def build_hotmount_mount_command(mount_token: VolumeMountTokenDto | AsyncVolumeMountTokenDto, mount_path: str) -> str:
    """Build the shell command that bootstraps the hotmount agent and mounts the volume.

    It exports the SEAWEED_* environment contract and runs the region's ``init.sh``, using
    passwordless sudo when not already root (sudo strips env, so the vars are passed via ``env``).
    """
    env_vars = {
        "SEAWEED_TOKEN": mount_token.token,
        "SEAWEED_GATEWAY_GRPC": mount_token.gateway_grpc,
        "SEAWEED_GATEWAY_HTTP": mount_token.gateway_http,
        "SEAWEED_BINARIES_URL": mount_token.binaries_url,
        "SEAWEED_MOUNT_DIR": mount_path,
        "SEAWEED_VERSION": mount_token.version,
    }
    env_assignments = " ".join(f"{key}='{value}'" for key, value in env_vars.items() if value)
    # The bootstrap (and init.sh itself) requires curl. Fail loudly if it is missing or the
    # download fails, rather than letting a broken ``curl ... | bash`` pipe exit 0 and mount nothing.
    inner = "; ".join(
        [
            "set -e",
            (
                'if ! command -v curl >/dev/null 2>&1; then echo "hotmount: curl is required to '
                + 'bootstrap the agent but was not found in the sandbox" >&2; exit 1; fi'
            ),
            'mkdir -p "$SEAWEED_MOUNT_DIR"',
            'init_script="$(curl -fsSL "$SEAWEED_BINARIES_URL/init.sh")"',
            'printf %s "$init_script" | bash',
        ]
    )
    return (
        'if [ "$(id -u)" != 0 ]; then SUDO="sudo -n"; else SUDO=""; fi; '
        f"$SUDO env {env_assignments} "
        f"bash -c '{inner}'"
    )


class VolumeMount(ApiVolumeMount, AsyncApiVolumeMount):  # pyright: ignore[reportIncompatibleVariableOverride]
    """Represents a Volume mount configuration for a Sandbox.

    Attributes:
        volume_id (str): ID or name of the volume to mount.
        mount_path (str): Path where the volume will be mounted in the sandbox.
        subpath (str | None): Optional S3 subpath/prefix within the volume to mount.
            When specified, only this prefix will be accessible. When omitted,
            the entire volume is mounted.
    """


class Volume(VolumeDto):
    """Represents a Daytona Volume which is a shared storage volume for Sandboxes.

    Attributes:
        id (str): Unique identifier for the Volume.
        name (str): Name of the Volume.
        organization_id (str): Organization ID of the Volume.
        state (str): State of the Volume.
        created_at (str): Date and time when the Volume was created.
        updated_at (str): Date and time when the Volume was last updated.
        last_used_at (str): Date and time when the Volume was last used.
    """

    @classmethod
    def from_dto(cls, dto: VolumeDto | AsyncVolumeDto) -> "Volume":
        return cls.model_validate(dto.model_dump())


class VolumePullResult(BaseModel):
    """Result of an explicit blockmount Volume pull into a running Sandbox.

    Attributes:
        volume_id (str): The Volume that was pulled.
        manifest_id (str | None): The merged manifest the Sandbox's scratch was advanced to.
        up_to_date (bool): True when the Sandbox already reflected the latest merged state.
        files_written (int): Files and symlinks written into the Sandbox by the pull.
        deleted (int): Paths removed because they were deleted in the merged state.
        skipped_local_newer (int): Paths left untouched because the Sandbox has a strictly newer
            local modification (the next commit's last-change-wins merge resolves them).
        bytes_fetched (int): Content bytes downloaded from the store.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(populate_by_name=True)

    volume_id: str = Field(alias="volumeId")
    manifest_id: str | None = Field(default=None, alias="manifestId")
    up_to_date: bool = Field(default=False, alias="upToDate")
    files_written: int = Field(default=0, alias="filesWritten")
    deleted: int = 0
    skipped_local_newer: int = Field(default=0, alias="skippedLocalNewer")
    bytes_fetched: int = Field(default=0, alias="bytesFetched")
