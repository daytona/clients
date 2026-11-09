# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""The --json report schema and the strict copy tool output is validated against.

The published schema stays open so reports from later 2.x releases validate;
the tool's own output is held to a strict copy: no unlisted fields and only the
values listed under ``examples`` (mirrored in the TypeScript tests/report.test.ts).
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).parents[2] / "mapping/report.schema.json"


def published_schema() -> dict[str, object]:
    schema = json.loads(SCHEMA_PATH.read_text())
    assert isinstance(schema, dict)
    return schema


def _tightened(node: object) -> object:
    if isinstance(node, list):
        return [_tightened(item) for item in node]
    if not isinstance(node, dict):
        return node
    copy = {
        key: value if key == "examples" else _tightened(value)
        for key, value in node.items()
    }
    if "properties" in copy:
        copy["additionalProperties"] = False
    if isinstance(copy.get("examples"), list):
        copy["enum"] = copy["examples"]
    return copy


def strict_schema() -> dict[str, object]:
    schema = _tightened(published_schema())
    assert isinstance(schema, dict)
    return schema


def validate_report(report: object) -> None:
    jsonschema.validate(report, strict_schema())
