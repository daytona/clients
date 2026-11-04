# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from ._core import TransportPayload, _expect_model_from_dict
from ._events import region_of
from ._gen.api import (
    AsyncOperation,
    AuditActionList,
    ForkSandboxOperation,
    ImagePage,
    OrganizationLimits,
    PreviewUrl,
    ProjectApiKeyWithContext,
    ProjectBudget,
    Sandbox,
    SandboxPage,
    SandboxPort,
    SandboxPortPage,
    Secret,
    SecretPage,
    SignedPreviewUrl,
    Snapshot,
    SnapshotPage,
    Volume,
    VolumePage,
)


def expect_audit_actions(value: TransportPayload) -> AuditActionList:
    return _expect_model_from_dict(
        value, AuditActionList, "audit actions response", AuditActionList.from_dict
    )


def expect_current_api_key(value: TransportPayload) -> ProjectApiKeyWithContext:
    return _expect_model_from_dict(
        value,
        ProjectApiKeyWithContext,
        "current API key response",
        ProjectApiKeyWithContext.from_dict,
    )


def expect_project_budget(value: TransportPayload) -> ProjectBudget:
    return _expect_model_from_dict(value, ProjectBudget, "project budget response", ProjectBudget.from_dict)


def expect_organization_limits(value: TransportPayload) -> OrganizationLimits:
    return _expect_model_from_dict(
        value, OrganizationLimits, "organization limits response", OrganizationLimits.from_dict
    )


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


def expect_list_sandboxes_resp(value: TransportPayload) -> SandboxPage:
    return _expect_model_from_dict(value, SandboxPage, "sandbox list response", SandboxPage.from_dict)


def expect_preview_endpoint(value: TransportPayload) -> SandboxPort:
    return _expect_model_from_dict(value, SandboxPort, "preview endpoint response", SandboxPort.from_dict)


def expect_daytona_preview_url(value: TransportPayload) -> PreviewUrl:
    return _expect_model_from_dict(value, PreviewUrl, "preview url response", PreviewUrl.from_dict)


def expect_secret(value: TransportPayload) -> Secret:
    return _expect_model_from_dict(value, Secret, "secret metadata response", Secret.from_dict)


def expect_signed_preview_url_resp(value: TransportPayload) -> SignedPreviewUrl:
    return _expect_model_from_dict(
        value,
        SignedPreviewUrl,
        "signed preview url response",
        SignedPreviewUrl.from_dict,
    )


def expect_sandbox_detail(value: TransportPayload) -> Sandbox:
    # Derive a missing region_id from the sandbox ID; empty means unknown.
    if isinstance(value, dict) and value.get("region_id") in (None, ""):
        sandbox_id = value.get("id")
        if isinstance(sandbox_id, str):
            region = region_of(sandbox_id)
            value = {**value, "region_id": "" if region == "_" else region}
    return _expect_model_from_dict(value, Sandbox, "sandbox detail response", Sandbox.from_dict)
