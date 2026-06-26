# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from daytona_api_client import CreateSecret, SecretApi, UpdateSecret

from .._utils.otel_decorator import with_instrumentation
from ..common.secret import CreateSecretParams, Secret, UpdateSecretParams


class SecretService:
    """Service for managing organization-scoped Daytona Secrets.

    Can be used to create, list, get, update and delete Secrets. Secrets can be mounted into
    Sandboxes as environment variables by referencing them via the ``secrets`` field on the
    create-sandbox parameters. The Sandbox only ever sees the Secret's opaque placeholder; the
    real value is substituted at the network egress layer for the Secret's allowed hosts.
    """

    def __init__(self, secret_api: SecretApi):
        self.__secret_api = secret_api

    def list(self) -> list[Secret]:
        """List all Secrets in the organization.

        Returns:
            list[Secret]: List of all Secrets in the organization.

        Example:
            ```python
            daytona = Daytona()
            secrets = daytona.secret.list()
            for secret in secrets:
                print(f"{secret.name} ({secret.id})")
            ```
        """
        return [Secret.from_dto(secret) for secret in self.__secret_api.list_secrets()]

    @with_instrumentation()
    def get(self, secret_id: str) -> Secret:
        """Get a Secret by its ID.

        Args:
            secret_id (str): ID of the Secret to retrieve.

        Returns:
            Secret: The requested Secret.

        Raises:
            NotFoundException: If the Secret does not exist.

        Example:
            ```python
            daytona = Daytona()
            secret = daytona.secret.get("secret-id")
            print(f"{secret.name} can be used on {', '.join(secret.hosts)}")
            ```
        """
        return Secret.from_dto(self.__secret_api.get_secret(secret_id))

    @with_instrumentation()
    def create(self, params: CreateSecretParams) -> Secret:
        """Create a new Secret.

        Args:
            params (CreateSecretParams): Parameters for the new Secret.

        Returns:
            Secret: The newly created Secret (without the plaintext ``value``).

        Raises:
            ApiException: If a Secret with the same name already exists in the organization (409).

        Example:
            ```python
            daytona = Daytona()
            secret = daytona.secret.create(
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
            self.__secret_api.create_secret(
                CreateSecret(
                    name=params.name,
                    value=params.value,
                    description=params.description,
                    hosts=params.hosts,
                )
            )
        )

    @with_instrumentation()
    def update(self, secret_id: str, params: UpdateSecretParams) -> Secret:
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
            daytona = Daytona()
            secret = daytona.secret.update(
                "secret-id",
                UpdateSecretParams(
                    value="sk-ant-new-value",
                    hosts=["api.anthropic.com", "*.anthropic.com"],
                ),
            )
            ```
        """
        return Secret.from_dto(
            self.__secret_api.update_secret(
                secret_id,
                UpdateSecret(
                    value=params.value,
                    description=params.description,
                    hosts=params.hosts,
                ),
            )
        )

    @with_instrumentation()
    def delete(self, secret_id: str) -> None:
        """Delete a Secret.

        Args:
            secret_id (str): ID of the Secret to delete.

        Raises:
            NotFoundException: If the Secret does not exist.

        Example:
            ```python
            daytona = Daytona()
            daytona.secret.delete("secret-id")
            print("Secret deleted")
            ```
        """
        self.__secret_api.delete_secret(secret_id)
