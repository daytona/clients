# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pydantic import BaseModel

from daytona_api_client import Secret as SecretDto
from daytona_api_client_async import Secret as AsyncSecretDto


class Secret(SecretDto):
    """Represents an organization-scoped Daytona Secret.

    The plaintext ``value`` is write-only and is never returned by the API. When a Secret is
    referenced from a Sandbox, the injected environment variable holds the opaque ``placeholder``
    token, not the real value. The real value is substituted transparently on outbound requests
    to the Secret's allowed ``hosts``.

    Attributes:
        id (str): Unique identifier for the Secret.
        name (str): Name of the Secret (unique within the organization).
        description (str | None): Optional description of the Secret.
        placeholder (str): Opaque token injected as the env var value in Sandboxes.
        hosts (list[str]): Hosts the Secret value may be sent to (may be empty).
        created_at (datetime): Date and time when the Secret was created.
        updated_at (datetime): Date and time when the Secret was last updated.
    """

    @classmethod
    def from_dto(cls, dto: SecretDto | AsyncSecretDto) -> "Secret":
        return cls.model_validate(dto.model_dump())


class CreateSecretParams(BaseModel):
    """Parameters for creating a new Secret.

    Attributes:
        name (str): Name of the Secret. Must match ``^[a-zA-Z_][a-zA-Z0-9_-]*$`` and be unique
            within the organization.
        value (str): The plaintext Secret value. Stored encrypted and never returned by the API.
        description (str | None): Optional description of the Secret.
        hosts (list[str] | None): Hosts the Secret value may be sent to. Each entry is a hostname
            (``api.example.com``) or a ``*.`` wildcard (``*.example.com``); ports are not supported.
            Omit to leave the Secret unrestricted.
    """

    name: str
    value: str
    description: str | None = None
    hosts: list[str] | None = None


class UpdateSecretParams(BaseModel):
    """Parameters for updating an existing Secret. Omitted fields are left unchanged.

    Attributes:
        value (str | None): Replaces the stored Secret value when present.
        description (str | None): Optional description of the Secret.
        hosts (list[str] | None): Hosts the Secret value may be sent to. Same constraints as
            :class:`CreateSecretParams.hosts`.
    """

    value: str | None = None
    description: str | None = None
    hosts: list[str] | None = None
