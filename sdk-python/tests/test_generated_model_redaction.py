# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Generated wire models keep credentials on the wire but out of diagnostics."""

from __future__ import annotations

import ast
import importlib
import json
import pkgutil
from pathlib import Path

import pytest
from _repo import _repo_file
from mark_sensitive import SENSITIVE_NAMES, mark
from pydantic import BaseModel

from daytona.v2._gen import api as api_models
from daytona.v2._gen import toolbox as toolbox_models
from daytona.v2._gen.api.models.build_context_upload import BuildContextUpload
from daytona.v2._gen.api.models.lookup_invitation_request import LookupInvitationRequest
from daytona.v2._gen.api.models.preview_url import PreviewUrl
from daytona.v2._gen.api.models.project_api_key_with_token import ProjectApiKeyWithToken
from daytona.v2._gen.api.models.set_registry_request import SetRegistryRequest
from daytona.v2._gen.api.models.signed_preview_url import SignedPreviewUrl
from daytona.v2._gen.toolbox.models.clone_request import CloneRequest

ROOT = Path(__file__).resolve().parents[1]
SECRET = "s3cr3t-credential-value"
PREVIEW_URL = "https://8080-sbx.example.test/"
REPO_URL = "https://git.example.test/repo.git"


def _generated_models() -> list[type[BaseModel]]:
    found: list[type[BaseModel]] = []
    for package in (api_models, toolbox_models):
        models = importlib.import_module(package.__name__ + ".models")
        for info in pkgutil.iter_modules(models.__path__):
            module = importlib.import_module(f"{models.__name__}.{info.name}")
            for value in vars(module).values():
                if (
                    isinstance(value, type)
                    and issubclass(value, BaseModel)
                    and value.__module__ == module.__name__
                ):
                    found.append(value)
    return found


@pytest.mark.parametrize(
    "model",
    [
        PreviewUrl(token=SECRET, url=PREVIEW_URL),
        LookupInvitationRequest(token=SECRET),
        CloneRequest(url=REPO_URL, path="/w", password=SECRET),
    ],
    ids=lambda model: type(model).__name__,
)
def test_diagnostics_redact_credentials_but_serialization_keeps_them(model: BaseModel) -> None:
    for diagnostic in (model.to_str(), repr(model), str(model)):  # type: ignore[attr-defined]
        assert SECRET not in diagnostic
        assert "<redacted>" in diagnostic

    assert SECRET in model.to_json()  # type: ignore[attr-defined]
    assert SECRET in json.dumps(model.to_dict())  # type: ignore[attr-defined]
    assert SECRET in json.dumps(model.model_dump(by_alias=True))


def test_redaction_keeps_other_fields_and_missing_credentials_visible() -> None:
    preview = PreviewUrl(token=SECRET, url=PREVIEW_URL)
    clone = CloneRequest(url=REPO_URL, path="/w")

    assert ast.literal_eval(preview.to_str()) == {
        "token": "<redacted>",
        "url": PREVIEW_URL,
        "additional_properties": {},
    }
    shown = ast.literal_eval(clone.to_str())
    assert shown["url"] == REPO_URL
    assert shown["password"] is None


def test_flattened_all_of_credentials_are_redacted() -> None:
    created = ProjectApiKeyWithToken.from_dict(
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "name": "ci",
            "project_id": "00000000-0000-0000-0000-000000000002",
            "project_permissions": [],
            "created_at": "2026-01-01T00:00:00Z",
            "sync_to_v0": False,
            "token": SECRET,
        }
    )
    assert created is not None

    assert SECRET not in created.to_str()
    assert SECRET not in repr(created)
    assert created.to_dict()["token"] == SECRET


def _contract_marked_fields() -> dict[str, frozenset[str]]:
    """The redaction set every generated model must carry, recomputed from the
    committed contracts with the generator's own marking pass."""
    expected: dict[str, set[str]] = {}
    for name in ("api-openapi.json", "toolbox-openapi.json"):
        spec = json.loads(_repo_file(f"contract/{name}").read_text())
        properties, models = mark(spec)
        for model in models:
            expected.setdefault(model, set())
        for model, prop in properties:
            expected[model].add(prop)
    return {model: frozenset(props) for model, props in expected.items()}


def test_generated_redaction_matches_the_marking_of_the_contract() -> None:
    expected = _contract_marked_fields()
    actual: dict[str, frozenset[str]] = {}
    for model in _generated_models():
        redacted = getattr(model, "_daytona_redacted_fields", None)
        if redacted is None:
            continue
        aliases = {name: field.alias or name for name, field in model.model_fields.items()}
        actual[model.__name__] = frozenset(aliases[name] for name in redacted)

    assert actual == expected
    for model, props in {
        "SetSecretRequest": {"value"},
        "SetOtelConfigRequest": {"headers"},
        "CloneRequest": {"username", "password"},
        "SignedPreviewUrl": {"token", "url"},
        "BuildContextUploadPart": {"url"},
    }.items():
        assert props <= expected[model], model


def test_every_generated_credential_named_field_is_redacted() -> None:
    missed = []
    for model in _generated_models():
        redacted = getattr(model, "_daytona_redacted_fields", frozenset())
        for name, field in model.model_fields.items():
            if (field.alias or name) in SENSITIVE_NAMES and name not in redacted:
                missed.append(f"{model.__name__}.{name}")
    assert missed == []


def test_signed_preview_url_hides_the_token_in_the_hostname() -> None:
    token = "s" + "0" * 32
    signed = SignedPreviewUrl.from_dict(
        {"token": token, "url": f"https://8080-{token}.example.test/", "expires_at": "2026-01-01T00:00:00Z"}
    )
    assert signed is not None

    for diagnostic in (signed.to_str(), repr(signed)):
        assert token not in diagnostic
    assert signed.to_dict()["url"] == f"https://8080-{token}.example.test/"


def test_build_context_part_urls_stay_out_of_diagnostics() -> None:
    url = f"https://bucket.example.test/ctx?partNumber=1&X-Amz-Signature={SECRET}"
    upload = BuildContextUpload.from_dict(
        {
            "id": "7f2c9a8e-0d6b-4c43-9a51-3f0d2c1b5e77",
            "state": "uploading",
            "region_id": "eu",
            "dockerfile": "Dockerfile",
            "bytes": 1,
            "sha256": "0" * 64,
            "parts": [{"part_number": 1, "bytes": 1, "url": url}],
        }
    )
    assert upload is not None

    for model in (upload, upload.parts[0]):
        for diagnostic in (model.to_str(), repr(model)):
            assert SECRET not in diagnostic
    assert upload.to_dict()["parts"][0]["url"] == url


def test_credential_named_extras_are_redacted() -> None:
    registry = SetRegistryRequest.from_dict(
        {"password": SECRET, "username": "ci", "token": SECRET, "region": "eu"}
    )
    assert registry is not None

    for diagnostic in (registry.to_str(), repr(registry)):
        assert SECRET not in diagnostic
        assert "'region': 'eu'" in diagnostic
    assert registry.to_dict()["token"] == SECRET


def test_marking_follows_contract_markers_names_and_nesting() -> None:
    spec = {
        "components": {
            "schemas": {
                "Creds": {
                    "properties": {
                        "token": {"type": "string"},
                        "pin": {"type": "string", "format": "password"},
                        "note": {"type": "string", "writeOnly": True},
                        "headers": {
                            "type": "object",
                            "additionalProperties": {"type": "string", "writeOnly": True},
                        },
                        "name": {"type": "string"},
                    }
                },
                "Holder": {
                    "properties": {
                        "items": {"type": "array", "items": {"$ref": "#/components/schemas/Creds"}}
                    }
                },
                "Outer": {"allOf": [{"$ref": "#/components/schemas/Holder"}]},
                "Plain": {"properties": {"name": {"type": "string"}}},
            }
        }
    }

    properties, models = mark(spec)

    assert properties == {("Creds", "token"), ("Creds", "pin"), ("Creds", "note"), ("Creds", "headers")}
    assert models == {"Creds", "Holder", "Outer"}
    schemas = spec["components"]["schemas"]
    assert schemas["Creds"]["properties"]["token"]["x-daytona-redact"] is True
    assert "x-daytona-redact" not in schemas["Creds"]["properties"]["name"]
    assert "x-daytona-redact-model" not in schemas["Plain"]


def test_marking_does_not_touch_the_committed_contract() -> None:
    for name in ("api-openapi.json", "toolbox-openapi.json"):
        text = _repo_file(f"contract/{name}").read_text()
        assert "x-daytona-redact" not in text
