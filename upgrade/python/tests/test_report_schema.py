# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The published report schema accepts what a later 2.x release adds; tool output may not."""

from __future__ import annotations

import jsonschema
from report_schema import published_schema, strict_schema

LATER_REPORT = {
    "tool": "daytona-upgrade",
    "version": "2.1.0",
    "language": "py",
    "mode": "upgrade",
    "root": "/project",
    "dryRun": False,
    "changedFiles": [],
    "dependencies": [],
    "nextSteps": [],
    "laterField": True,
    "items": [
        {
            "file": "app.py",
            "line": 1,
            "column": 1,
            "kind": "later-kind",
            "message": "Review this call.",
            "laterItemField": "x",
        }
    ],
    "refused": {"reason": "later-reason", "message": "Refused."},
}


def _errors(schema: dict[str, object]) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return sorted(
        "".join(f"/{part}" for part in error.absolute_path) + f" {error.validator}"
        for error in validator.iter_errors(LATER_REPORT)
    )


def test_accepts_fields_and_values_a_later_release_adds() -> None:
    # Given
    schema = published_schema()
    # When
    errors = _errors(schema)
    # Then
    assert errors == []


def test_holds_tool_output_to_the_listed_fields_and_values() -> None:
    # Given
    schema = strict_schema()
    # When
    errors = _errors(schema)
    # Then
    assert errors == [
        " additionalProperties",
        "/items/0 additionalProperties",
        "/items/0/kind enum",
        "/refused/reason enum",
    ]
