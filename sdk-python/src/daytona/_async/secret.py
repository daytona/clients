# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from daytona_api_client_async import CreateSecret, SecretApi, UpdateSecret

from .._utils.otel_decorator import with_instrumentation
from ..common.errors import DaytonaValidationError
from ..common.secret import CreateSecretParams, ListSecretsResponse, Secret, UpdateSecretParams


class AsyncSecretService:
    """Service for managing organization-scoped Daytona Secrets.

    Can be used to create, list, get, update and delete Secrets. Secrets can be mounted into
    Sandboxes as environment variables by referencing them via the ``secrets`` field on the
    create-sandbox parameters. The Sandbox only ever sees the Secret's opaque placeholder; the
    real value is substituted at the network egress layer for the Secret's allowed hosts.
    """

    def __init__(self, secret_api: SecretApi):
        self.__secret_api = secret_api

    @with_instrumentation()
    async def list(
        self,
        cursor: str | None = None,
        limit: int | None = None,
        name: str | None = None,
        sort: str | None = None,
        order: str | None = None,
    ) -> ListSecretsResponse:
        """List Secrets in the organization using cursor-based pagination.

        Args:
            cursor (str | None): Pagination cursor from a previous response. Omit to start
                from the first page.
            limit (int | None): Number of results per page (1-200). Defaults to 100.
            name (str | None): Filter by partial name match.
            sort (str | None): Field to sort by (``name``, ``createdAt`` or ``updatedAt``).
                Defaults to ``createdAt``.
            order (str | None): Direction to sort by (``asc`` or ``desc``). Defaults to ``desc``.

        Returns:
            ListSecretsResponse: The current page of Secrets, the total number of Secrets
                matching the filters and the cursor for the next page (``None`` when there
                are no more pages).

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                cursor = None
                while True:
                    page = await daytona.secret.list(cursor=cursor, limit=50)
                    for secret in page.items:
                        print(f"{secret.name} ({secret.id})")
                    if page.next_cursor is None:
                        break
                    cursor = page.next_cursor
            ```
        """
        if limit is not None and limit < 1:
            raise DaytonaValidationError("limit must be a positive integer")

        response = await self.__secret_api.list_secrets_paginated(
            cursor=cursor, limit=limit, name=name, sort=sort, order=order
        )
        return ListSecretsResponse(
            items=[Secret.from_dto(secret) for secret in response.items],
            total=response.total,
            next_cursor=response.next_cursor,
        )

    @with_instrumentation()
    async def get(self, secret_id: str) -> Secret:
        """Get a Secret by its ID.

        Args:
            secret_id (str): ID of the Secret to retrieve.

        Returns:
            Secret: The requested Secret.

        Raises:
            NotFoundException: If the Secret does not exist.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                secret = await daytona.secret.get("secret-id")
                print(f"{secret.name} can be used on {', '.join(secret.hosts)}")
            ```
        """
        return Secret.from_dto(await self.__secret_api.get_secret(secret_id))

    @with_instrumentation()
    async def create(self, params: CreateSecretParams) -> Secret:
        """Create a new Secret.

        Args:
            params (CreateSecretParams): Parameters for the new Secret.

        Returns:
            Secret: The newly created Secret (without the plaintext ``value``).

        Raises:
            ApiException: If a Secret with the same name already exists in the organization (409).

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                secret = await daytona.secret.create(
                    CreateSecretParams(
                        name="anthropic-prod",
                        value="sk-ant-...",
                        hosts=["api.anthropic.com"],
                    )
                )
                print(f"Created secret {secret.name} with placeholder {secret.placeholder}")
            ```
        """
        return Secret.from_dto(
            await self.__secret_api.create_secret(
                CreateSecret(
                    name=params.name,
                    value=params.value,
                    description=params.description,
                    hosts=params.hosts,
                )
            )
        )

    @with_instrumentation()
    async def update(self, secret_id: str, params: UpdateSecretParams) -> Secret:
        """Update an existing Secret. Omitted fields are left unchanged.

        Args:
            secret_id (str): ID of the Secret to update.
            params (UpdateSecretParams): Fields to update.

        Returns:
            Secret: The updated Secret.

        Raises:
            NotFoundException: If the Secret does not exist.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                secret = await daytona.secret.update(
                    "secret-id",
                    UpdateSecretParams(
                        value="sk-ant-new-value",
                        hosts=["api.anthropic.com", "*.anthropic.com"],
                    ),
                )
            ```
        """
        return Secret.from_dto(
            await self.__secret_api.update_secret(
                secret_id,
                UpdateSecret(
                    value=params.value,
                    description=params.description,
                    hosts=params.hosts,
                ),
            )
        )

    @with_instrumentation()
    async def delete(self, secret_id: str) -> None:
        """Delete a Secret.

        Args:
            secret_id (str): ID of the Secret to delete.

        Raises:
            NotFoundException: If the Secret does not exist.

        Example:
            ```python
            async with AsyncDaytona() as daytona:
                await daytona.secret.delete("secret-id")
                print("Secret deleted")
            ```
        """
        await self.__secret_api.delete_secret(secret_id)
