# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from daytona.common.daytona import CreateSandboxFromSnapshotParams
from daytona.common.secret import CreateSecretParams, Secret, UpdateSecretParams
from daytona_api_client import Secret as SecretDto


def _make_secret_dto(name="test-secret", secret_id="secret-123"):
    return SecretDto(
        id=secret_id,
        name=name,
        description="a description",
        placeholder="dtn_secret_123",
        hosts=["api.example.com"],
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
    )


class TestSyncSecretService:
    def _make_service(self):
        from daytona._sync.secret import SecretService

        mock_api = MagicMock()
        return SecretService(mock_api), mock_api

    def test_list(self):
        service, api = self._make_service()
        api.list_secrets.return_value = [_make_secret_dto()]
        result = service.list()
        assert len(result) == 1
        assert isinstance(result[0], Secret)

    def test_get(self):
        service, api = self._make_service()
        api.get_secret.return_value = _make_secret_dto()
        result = service.get("secret-123")
        assert isinstance(result, Secret)
        api.get_secret.assert_called_once_with("secret-123")

    def test_get_not_found_raises(self):
        from daytona_api_client.exceptions import NotFoundException

        service, api = self._make_service()
        api.get_secret.side_effect = NotFoundException(status=404, reason="Not found")
        with pytest.raises(NotFoundException):
            service.get("nonexistent")

    def test_create(self):
        service, api = self._make_service()
        api.create_secret.return_value = _make_secret_dto(name="new-secret")
        result = service.create(
            CreateSecretParams(
                name="new-secret",
                value="super-secret",
                description="a description",
                hosts=["api.example.com", "*.example.com"],
            )
        )
        assert isinstance(result, Secret)
        # The plaintext value is write-only and never read back.
        assert not hasattr(result, "value")
        create_secret = api.create_secret.call_args.args[0]
        assert create_secret.name == "new-secret"
        assert create_secret.value == "super-secret"
        assert create_secret.description == "a description"
        assert create_secret.hosts == ["api.example.com", "*.example.com"]

    def test_create_duplicate_name_raises(self):
        from daytona_api_client.exceptions import ApiException

        service, api = self._make_service()
        api.create_secret.side_effect = ApiException(status=409, reason="Conflict")
        with pytest.raises(ApiException):
            service.create(CreateSecretParams(name="dup", value="v"))

    def test_update(self):
        service, api = self._make_service()
        api.update_secret.return_value = _make_secret_dto()
        result = service.update("secret-123", UpdateSecretParams(value="new-value"))
        assert isinstance(result, Secret)
        secret_id, update_secret = api.update_secret.call_args.args
        assert secret_id == "secret-123"
        assert update_secret.value == "new-value"
        assert update_secret.description is None
        assert update_secret.hosts is None

    def test_delete(self):
        service, api = self._make_service()
        api.delete_secret.return_value = None
        service.delete("secret-123")
        api.delete_secret.assert_called_once_with("secret-123")


class TestAsyncSecretService:
    def _make_service(self):
        from daytona._async.secret import AsyncSecretService

        mock_api = AsyncMock()
        return AsyncSecretService(mock_api), mock_api

    @pytest.mark.asyncio
    async def test_list(self):
        service, api = self._make_service()
        api.list_secrets.return_value = [_make_secret_dto()]
        result = await service.list()
        assert len(result) == 1
        assert isinstance(result[0], Secret)

    @pytest.mark.asyncio
    async def test_get(self):
        service, api = self._make_service()
        api.get_secret.return_value = _make_secret_dto()
        result = await service.get("secret-123")
        assert isinstance(result, Secret)
        api.get_secret.assert_called_once_with("secret-123")

    @pytest.mark.asyncio
    async def test_create(self):
        service, api = self._make_service()
        api.create_secret.return_value = _make_secret_dto(name="new-secret")
        result = await service.create(
            CreateSecretParams(name="new-secret", value="super-secret", hosts=["api.example.com"])
        )
        assert isinstance(result, Secret)
        create_secret = api.create_secret.call_args.args[0]
        assert create_secret.name == "new-secret"
        assert create_secret.value == "super-secret"
        assert create_secret.hosts == ["api.example.com"]

    @pytest.mark.asyncio
    async def test_update(self):
        service, api = self._make_service()
        api.update_secret.return_value = _make_secret_dto()
        result = await service.update("secret-123", UpdateSecretParams(value="new-value"))
        assert isinstance(result, Secret)
        secret_id, update_secret = api.update_secret.call_args.args
        assert secret_id == "secret-123"
        assert update_secret.value == "new-value"

    @pytest.mark.asyncio
    async def test_delete(self):
        service, api = self._make_service()
        await service.delete("secret-123")
        api.delete_secret.assert_called_once_with("secret-123")


class TestCreateSandboxSecretsSerialization:
    """The ``secrets`` map (env var name -> existing secret name) must serialize to the generated
    ``List[Dict[str, str]]`` form as an array of single-key dicts."""

    def _build_secrets(self, secrets):
        if not secrets:
            return None
        return [{env_var: secret_name} for env_var, secret_name in secrets.items()]

    def test_secrets_map_to_single_key_dicts(self):
        params = CreateSandboxFromSnapshotParams(
            secrets={"ANTHROPIC_API_KEY": "anthropic-prod", "OPENAI_API_KEY": "openai-prod"}
        )
        result = self._build_secrets(params.secrets)
        assert result == [
            {"ANTHROPIC_API_KEY": "anthropic-prod"},
            {"OPENAI_API_KEY": "openai-prod"},
        ]

    def test_secrets_serializes_into_create_sandbox(self):
        from daytona_api_client import CreateSandbox

        params = CreateSandboxFromSnapshotParams(secrets={"ANTHROPIC_API_KEY": "anthropic-prod"})
        secrets = self._build_secrets(params.secrets)
        sandbox_data = CreateSandbox(secrets=secrets)
        assert sandbox_data.secrets == [{"ANTHROPIC_API_KEY": "anthropic-prod"}]

    def test_no_secrets_serializes_to_none(self):
        params = CreateSandboxFromSnapshotParams()
        assert self._build_secrets(params.secrets) is None
