# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The published report schema accepts what a later 2.x release adds; tool output may not."""

from __future__ import annotations

from collections.abc import Iterator

import jsonschema
from report_schema import published_schema, strict_schema

LATER_REPORT = {
    "tool": "daytona-upgrade",
    "version": "2.1.0",
    "language": "later-language",
    "mode": "later-mode",
    "root": "/project",
    "dryRun": False,
    "changedFiles": [],
    "dependencies": [
        {
            "file": "requirements.txt",
            "from": "daytona==0.230.0",
            "to": "daytona[v0-sdk]>=2,<3",
            "laterDependencyField": "x",
        }
    ],
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
            "suggestion": {
                "id": "sandbox.process.exec",
                "receiver": "sandbox.process",
                "v0": "sandbox.process.exec",
                "v2": "sandbox.process.run",
                "category": "later-category",
                "notes": [],
                "docs": "https://www.daytona.io/docs",
                "laterSuggestionField": "x",
            },
        }
    ],
    "refused": {
        "reason": "later-reason",
        "message": "Refused.",
        "laterRefusedField": "x",
    },
    "warnings": [
        {"file": "app.py", "message": "Review this.", "laterWarningField": "x"}
    ],
}


def _errors(schema: dict[str, object]) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return sorted(
        "".join(f"/{part}" for part in error.absolute_path) + f" {error.validator}"
        for error in validator.iter_errors(LATER_REPORT)
    )


def _nodes(node: object) -> Iterator[dict[str, object]]:
    if isinstance(node, list):
        for item in node:
            yield from _nodes(item)
    elif isinstance(node, dict):
        yield node
        for key, value in node.items():
            if key != "examples":
                yield from _nodes(value)


def test_accepts_fields_and_values_a_later_release_adds() -> None:
    # Given
    schema = published_schema()
    # When
    errors = _errors(schema)
    # Then
    assert errors == []


def test_publishes_no_closed_object_or_vocabulary() -> None:
    # Given
    nodes = list(_nodes(published_schema()))
    # When
    closed = [
        node
        for node in nodes
        if node.get("additionalProperties") is False
        or node.get("unevaluatedProperties") is False
    ]
    enums = [node for node in nodes if "enum" in node]
    untyped = [
        node for node in nodes if "examples" in node and node.get("type") != "string"
    ]
    # Then
    assert closed == []
    assert enums == []
    assert untyped == []


def test_holds_tool_output_to_the_listed_fields_and_values() -> None:
    # Given
    schema = strict_schema()
    # When
    errors = _errors(schema)
    # Then
    assert errors == [
        " additionalProperties",
        "/dependencies/0 additionalProperties",
        "/items/0 additionalProperties",
        "/items/0/kind enum",
        "/items/0/suggestion additionalProperties",
        "/items/0/suggestion/category enum",
        "/language enum",
        "/mode enum",
        "/refused additionalProperties",
        "/refused/reason enum",
        "/warnings/0 additionalProperties",
    ]
