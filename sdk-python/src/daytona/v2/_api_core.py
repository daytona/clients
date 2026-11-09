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
from .errors import DaytonaError, create_error
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


def _id_only(value: TransportPayload, fields: tuple[str, ...]) -> TransportPayload:
    """An accepted create's response, of which only the ``id`` is required:
    the resource exists once its id came back, so each of ``fields`` that is
    missing or not a string reads as empty (the caller falls back), never an
    error. TypeScript checks only the id too."""
    if not isinstance(value, dict):
        return value
    return {**value, **{name: "" for name in fields if not isinstance(value.get(name), str)}}


def expect_create_image_resp(value: TransportPayload) -> AsyncOperation:
    return _expect_model_from_dict(
        _id_only(value, ("status",)), AsyncOperation, "image create response", AsyncOperation.from_dict
    )


def expect_create_snapshot_resp(value: TransportPayload) -> AsyncOperation:
    return _expect_model_from_dict(
        _id_only(value, ("status",)), AsyncOperation, "snapshot create response", AsyncOperation.from_dict
    )


def expect_endpoint_list_resp(value: TransportPayload) -> SandboxPortPage:
    return _expect_model_from_dict(value, SandboxPortPage, "preview endpoint list", SandboxPortPage.from_dict)


def expect_fork_resp(value: TransportPayload) -> ForkSandboxOperation:
    # The fork exists once its id came back, as for a create: a missing or
    # non-string status reads as `unknown`, `forked_from` falls back to the
    # source's id, and the toolbox URL comes from the fork's detail read.
    return _expect_model_from_dict(
        _id_only(value, ("status", "forked_from", "toolbox_proxy_url")),
        ForkSandboxOperation,
        "sandbox fork response",
        ForkSandboxOperation.from_dict,
    )


def _without_item_cursors(value: TransportPayload, items: str) -> TransportPayload:
    """A list page whose items' ``cursor`` is not read: neither SDK reads the
    per-item cursor (the page's ``next_cursor`` continues the list), so a
    missing or mistyped one is not a contract error, as in TypeScript."""
    if not isinstance(value, dict) or not isinstance(value.get(items), list):
        return value
    entries = [{**entry, "cursor": ""} if isinstance(entry, dict) else entry for entry in value[items]]
    return {**value, items: entries}


def expect_images_page(value: TransportPayload) -> ImagePage:
    # Every contract-required field (`platform`, `default` and `deprecated`
    # included) must be present: a missing one is the malformed-response error.
    return _expect_model_from_dict(
        _without_item_cursors(value, "images"), ImagePage, "image list response", ImagePage.from_dict
    )


def expect_image(value: TransportPayload) -> Image:
    """An image detail checked against the contract (as the list items are) before it is mapped."""
    wire = _expect_model_from_dict(value, _gen.api.Image, "image detail response", _gen.api.Image.from_dict)
    return parse_image(wire)


def expect_list_secrets_resp(value: TransportPayload) -> SecretPage:
    return _expect_model_from_dict(value, SecretPage, "secret list response", SecretPage.from_dict)


def expect_snapshot(value: TransportPayload) -> Snapshot:
    return _expect_model_from_dict(value, Snapshot, "snapshot detail response", Snapshot.from_dict)


def expect_list_snapshots_resp(value: TransportPayload) -> SnapshotPage:
    return _expect_model_from_dict(
        _without_item_cursors(value, "snapshots"),
        SnapshotPage,
        "snapshot list response",
        SnapshotPage.from_dict,
    )


def expect_volume(value: TransportPayload) -> Volume:
    return _expect_model_from_dict(value, Volume, "volume detail response", Volume.from_dict)


def expect_list_volumes_resp(value: TransportPayload) -> VolumePage:
    return _expect_model_from_dict(value, VolumePage, "volume list response", VolumePage.from_dict)


def expect_preview_endpoint(value: TransportPayload) -> SandboxPort:
    return _expect_model_from_dict(value, SandboxPort, "preview endpoint response", SandboxPort.from_dict)


def expect_daytona_preview_url(value: TransportPayload) -> PreviewUrl:
    return _expect_model_from_dict(value, PreviewUrl, "preview url response", PreviewUrl.from_dict)


_LIFECYCLE_INTEGERS = ("timeout_seconds", "idle_expiry_seconds")
_LIFECYCLE_STRINGS = (
    "on_timeout",
    "timeout_at",
    "on_idle_timeout",
    "idle_timeout_at",
    "last_activity_at",
    "last_activity_source",
)


def expect_sandbox_lifecycle_policy(value: TransportPayload) -> JsonObject:
    """The lifecycle echo of ``PATCH .../lifecycle-policy`` and
    ``POST .../reset-idle-timer`` (the contract's ``SandboxLifecyclePolicy``),
    checked field by field as TypeScript checks it: a required string ``id``
    and ``lifecycle_policy`` object, whose optional fields are 32-bit integers
    or strings when present. Timestamps stay strings here: an unparseable
    activity clock is a degenerate echo the handle keeps out of its cache, not
    a malformed response. The raw object is returned because the handle keeps
    the wire timestamps verbatim."""
    what = "sandbox lifecycle policy response"

    def malformed(message: str) -> DaytonaError:
        return create_error(f"{what} {message}", 400, "BAD_REQUEST", "SDK")

    if not isinstance(value, dict):
        raise malformed("is not a JSON object")
    data = expect_object(value)
    if "id" not in data or data["id"] is None:
        raise malformed("is missing its id")
    if not isinstance(data["id"], str):
        raise malformed("has an id that is not a string")
    policy = data.get("lifecycle_policy")
    if policy is None:
        raise malformed("is missing its lifecycle_policy")
    if not isinstance(policy, dict):
        raise malformed("has a lifecycle_policy that is not an object")
    for key in _LIFECYCLE_INTEGERS:
        number = policy.get(key)
        if number is not None and (
            isinstance(number, bool) or not isinstance(number, int) or not -(2**31) <= number < 2**31
        ):
            raise malformed(f"has a lifecycle_policy.{key} that is not a 32-bit integer")
    for key in _LIFECYCLE_STRINGS:
        text = policy.get(key)
        if text is not None and not isinstance(text, str):
            raise malformed(f"has a lifecycle_policy.{key} that is not a string")
    return data


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
    # The contract requires region_id (a missing one is malformed); an empty
    # one is derived from the sandbox ID, and stays empty when it cannot be.
    if isinstance(value, dict) and value.get("region_id") == "":
        sandbox_id = value.get("id")
        if isinstance(sandbox_id, str):
            region = region_of(sandbox_id)
            value = {**value, "region_id": "" if region == "_" else region}
    return value
