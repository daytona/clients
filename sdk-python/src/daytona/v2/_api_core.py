# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from datetime import datetime

from . import _gen
from ._core import TransportPayload, _expect_model_from_dict, expect_object, parse_image, parse_timestamp
from ._events import region_of
from ._gen.api import (
    AsyncOperation,
    ForkSandboxOperation,
    ImagePage,
    PreviewUrl,
    Sandbox,
    SandboxPort,
    SandboxPortPage,
    SecretPage,
    SignedPreviewUrl,
    Snapshot,
    SnapshotPage,
    Volume,
    VolumePage,
)
from .errors import create_error
from .types import (
    AuditActionGroup,
    AuditActionList,
    Image,
    ImageBuildLimits,
    JsonObject,
    MacCatalog,
    MacCatalogModel,
    MacCatalogRegion,
    MacModel,
    MacPrice,
    MacResources,
    MemberLimits,
    OrganizationLimits,
    ProjectApiKey,
    ProjectApiKeyContext,
    ProjectBudget,
    SandboxLimits,
    SandboxResourceLimits,
    Secret,
    StorageLimits,
    UsageLimit,
)


def expect_audit_actions(value: TransportPayload) -> AuditActionList:
    wire = _expect_model_from_dict(
        value, _gen.api.AuditActionList, "audit actions response", _gen.api.AuditActionList.from_dict
    )
    return AuditActionList(
        targets=tuple(
            AuditActionGroup(target_type=group.target_type, actions=tuple(group.actions))
            for group in wire.targets
        )
    )


def expect_current_api_key(value: TransportPayload) -> ProjectApiKey:
    wire = _expect_model_from_dict(
        value,
        _gen.api.ProjectApiKeyWithContext,
        "current API key response",
        _gen.api.ProjectApiKeyWithContext.from_dict,
    )
    return ProjectApiKey(
        id=wire.id,
        name=wire.name,
        project_id=wire.project_id,
        project_permissions=tuple(wire.project_permissions),
        created_at=_timestamp(wire.created_at),
        context=ProjectApiKeyContext(
            org_id=wire.context.org_id,
            org_name=wire.context.org_name,
            project_name=wire.context.project_name,
        ),
        created_by=wire.created_by,
        expires_at=parse_timestamp(wire.expires_at),
        last_used_at=parse_timestamp(wire.last_used_at),
        revoked_at=parse_timestamp(wire.revoked_at),
        key_prefix=wire.key_prefix,
        key_suffix=wire.key_suffix,
        service_account_id=wire.service_account_id,
        service_account_name=wire.service_account_name,
    )


def expect_project_budget(value: TransportPayload) -> ProjectBudget:
    wire = _expect_model_from_dict(
        value, _gen.api.ProjectBudget, "project budget response", _gen.api.ProjectBudget.from_dict
    )
    return ProjectBudget(
        period_start_date=wire.period_start_date,
        monthly_limit_cents=wire.monthly_limit_cents,
        spent_cents=wire.spent_cents,
        created_at=parse_timestamp(wire.created_at),
        updated_at=parse_timestamp(wire.updated_at),
        evaluated_at=parse_timestamp(wire.evaluated_at),
        exceeded_at=parse_timestamp(wire.exceeded_at),
        stop_at=parse_timestamp(wire.stop_at),
    )


def _usage_limit(wire: _gen.api.UsageLimit) -> UsageLimit:
    return UsageLimit(limit=wire.limit, usage=wire.usage)


def expect_organization_limits(value: TransportPayload) -> OrganizationLimits:
    wire = _expect_model_from_dict(
        value,
        _gen.api.OrganizationLimits,
        "organization limits response",
        _gen.api.OrganizationLimits.from_dict,
    )
    return OrganizationLimits(
        sandboxes=SandboxLimits(
            concurrent=_usage_limit(wire.sandboxes.concurrent), total=_usage_limit(wire.sandboxes.total)
        ),
        image_builds=ImageBuildLimits(concurrent=_usage_limit(wire.image_builds.concurrent)),
        storage=StorageLimits(
            volumes=_usage_limit(wire.storage.volumes),
            images=_usage_limit(wire.storage.images),
            snapshots=_usage_limit(wire.storage.snapshots),
        ),
        secrets_per_project=_usage_limit(wire.secrets_per_project),
        api_keys_per_project=_usage_limit(wire.api_keys_per_project),
        api_keys_per_organization=_usage_limit(wire.api_keys_per_organization),
        sandbox_resources=SandboxResourceLimits(
            max_cores=float(wire.sandbox_resources.max_cores),
            max_memory_mib=wire.sandbox_resources.max_memory_mib,
        )
        if wire.sandbox_resources is not None
        else None,
        members=MemberLimits(
            limit=wire.members.limit,
            usage=wire.members.usage,
            pending_invitations=wire.members.pending_invitations,
        )
        if wire.members is not None
        else None,
    )


def secret_from_wire(wire: _gen.api.Secret) -> Secret:
    return Secret(
        name=wire.name,
        allowed_domains=tuple(wire.allowed_domains),
        created_at=_timestamp(wire.created_at),
        updated_at=_timestamp(wire.updated_at),
        description=wire.description,
    )


def mac_resources_from_wire(wire: _gen.api.MacResources) -> MacResources:
    return MacResources(cores=wire.cores, memory_mib=wire.memory_mib, disk_mib=wire.disk_mib)


def mac_model_from_wire(wire: _gen.api.MacModel) -> MacModel:
    return MacModel(
        id=wire.id,
        name=wire.name,
        cores=wire.cores,
        memory_mib=wire.memory_mib,
        disk_mib=wire.disk_mib,
        sandbox_disk_mib=wire.sandbox_disk_mib,
        max_sandboxes=wire.max_sandboxes,
        default_size=mac_resources_from_wire(wire.default_size) if wire.default_size is not None else None,
    )


def expect_mac_models(value: TransportPayload) -> list[MacModel]:
    wire = _expect_model_from_dict(
        value, _gen.api.MacModelList, "Mac model list response", _gen.api.MacModelList.model_validate
    )
    return [mac_model_from_wire(model) for model in wire.mac_models]


def expect_mac_catalog(value: TransportPayload) -> MacCatalog:
    wire = _expect_model_from_dict(
        value, _gen.api.MacCatalog, "Mac catalog response", _gen.api.MacCatalog.model_validate
    )
    return MacCatalog(
        regions=tuple(
            MacCatalogRegion(
                id=region.id,
                default_model=region.default_model,
                models=tuple(
                    MacCatalogModel(
                        model=mac_model_from_wire(entry.model),
                        available=entry.available,
                        price=MacPrice(
                            cents_per_hour=float(entry.price.cents_per_hour),
                            period_cents=entry.price.period_cents,
                            period_seconds=entry.price.period_seconds,
                            currency=entry.price.currency,
                        )
                        if entry.price is not None
                        else None,
                    )
                    for entry in region.models
                ),
            )
            for region in wire.regions
        )
    )


def _timestamp(value: object) -> datetime:
    parsed = parse_timestamp(value)
    if parsed is None:
        raise create_error("API returned an invalid timestamp", 400, "BAD_REQUEST", "SDK")
    return parsed


def expect_create_image_resp(value: TransportPayload) -> AsyncOperation:
    return _expect_model_from_dict(value, AsyncOperation, "image create response", AsyncOperation.from_dict)


def expect_create_snapshot_resp(value: TransportPayload) -> AsyncOperation:
    return _expect_model_from_dict(
        value, AsyncOperation, "snapshot create response", AsyncOperation.from_dict
    )


def expect_endpoint_list_resp(value: TransportPayload) -> SandboxPortPage:
    return _expect_model_from_dict(value, SandboxPortPage, "preview endpoint list", SandboxPortPage.from_dict)


def expect_fork_resp(value: TransportPayload) -> ForkSandboxOperation:
    return _expect_model_from_dict(
        value, ForkSandboxOperation, "sandbox fork response", ForkSandboxOperation.from_dict
    )


#: Missing image classification flags default to False.
_IMAGE_PLATFORM_FLAGS = ("platform", "default", "deprecated")


def expect_images_page(value: TransportPayload) -> ImagePage:
    if isinstance(value, dict) and isinstance(value.get("images"), list):
        images = value["images"]
        assert isinstance(images, list)
        value = {
            **value,
            "images": [
                {**{flag: False for flag in _IMAGE_PLATFORM_FLAGS}, **image}
                if isinstance(image, dict)
                else image
                for image in images
            ],
        }
    return _expect_model_from_dict(value, ImagePage, "image list response", ImagePage.from_dict)


def expect_image(value: TransportPayload) -> Image:
    """An image detail checked against the contract (as the list items are) before it is mapped."""
    if isinstance(value, dict):
        value = {**{flag: False for flag in _IMAGE_PLATFORM_FLAGS}, **value}
    wire = _expect_model_from_dict(value, _gen.api.Image, "image detail response", _gen.api.Image.from_dict)
    return parse_image(wire)


def expect_list_secrets_resp(value: TransportPayload) -> SecretPage:
    return _expect_model_from_dict(value, SecretPage, "secret list response", SecretPage.from_dict)


def expect_snapshot(value: TransportPayload) -> Snapshot:
    return _expect_model_from_dict(value, Snapshot, "snapshot detail response", Snapshot.from_dict)


def expect_list_snapshots_resp(value: TransportPayload) -> SnapshotPage:
    return _expect_model_from_dict(value, SnapshotPage, "snapshot list response", SnapshotPage.from_dict)


def expect_volume(value: TransportPayload) -> Volume:
    return _expect_model_from_dict(value, Volume, "volume detail response", Volume.from_dict)


def expect_list_volumes_resp(value: TransportPayload) -> VolumePage:
    return _expect_model_from_dict(value, VolumePage, "volume list response", VolumePage.from_dict)


def expect_preview_endpoint(value: TransportPayload) -> SandboxPort:
    return _expect_model_from_dict(value, SandboxPort, "preview endpoint response", SandboxPort.from_dict)


def expect_daytona_preview_url(value: TransportPayload) -> PreviewUrl:
    return _expect_model_from_dict(value, PreviewUrl, "preview url response", PreviewUrl.from_dict)


def expect_secret(value: TransportPayload) -> Secret:
    return secret_from_wire(
        _expect_model_from_dict(value, _gen.api.Secret, "secret metadata response", _gen.api.Secret.from_dict)
    )


def expect_signed_preview_url_resp(value: TransportPayload) -> SignedPreviewUrl:
    return _expect_model_from_dict(
        value,
        SignedPreviewUrl,
        "signed preview url response",
        SignedPreviewUrl.from_dict,
    )


def expect_sandbox_detail(value: TransportPayload) -> Sandbox:
    return _expect_model_from_dict(_with_region(value), Sandbox, "sandbox detail response", Sandbox.from_dict)


def sandbox_detail_wire(value: TransportPayload) -> JsonObject:
    """A validated sandbox detail as its wire object, its strings (timestamps) verbatim."""
    value = _with_region(value)
    expect_sandbox_detail(value)
    return expect_object(value)


def _with_region(value: TransportPayload) -> TransportPayload:
    # Derive a missing region_id from the sandbox ID; empty means unknown.
    if isinstance(value, dict) and value.get("region_id") in (None, ""):
        sandbox_id = value.get("id")
        if isinstance(sandbox_id, str):
            region = region_of(sandbox_id)
            value = {**value, "region_id": "" if region == "_" else region}
    return value
