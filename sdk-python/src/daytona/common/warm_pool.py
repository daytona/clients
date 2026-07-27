from __future__ import annotations

from daytona_api_client import WarmPool as WarmPoolDto
from daytona_api_client_async import WarmPool as AsyncWarmPoolDto


class WarmPool(WarmPoolDto):
    """Represents a Daytona Warm Pool which keeps ready-to-use Sandboxes for a snapshot.

    ``current_size`` versus ``pool`` is the pool's status: ``current_size`` is the number
    of ready warm sandboxes, ``pool`` is the desired number. ``error_reason`` is set when
    the pool cannot be filled.

    Attributes:
        id (str): Unique identifier for the Warm Pool.
        organization_id (str): Organization ID that owns the Warm Pool.
        snapshot (str): Snapshot the pool keeps warm sandboxes for.
        target (str): Target region of the pool.
        pool (int): Desired number of warm sandboxes.
        current_size (int): Current number of ready warm sandboxes in the pool.
        cpu (int): CPU cores per sandbox.
        mem (int): Memory per sandbox in GiB.
        disk (int): Disk per sandbox in GiB.
        os_user (str): OS user of the warm sandboxes.
        env (dict[str, str]): Environment variables of the warm sandboxes.
        error_reason (str | None): Reason the pool cannot be filled, if any.
        created_at (str): Date and time when the Warm Pool was created.
        updated_at (str): Date and time when the Warm Pool was last updated.
    """

    @classmethod
    def from_dto(cls, dto: WarmPoolDto | AsyncWarmPoolDto) -> "WarmPool":
        return cls.model_validate(dto.model_dump())
