# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bounded external constructor evidence, never arbitrary factory return proofs."""

from __future__ import annotations

import ast
import re
from typing import Final

from ._project_index_model import ProjectIndex

_KNOWN_FACTORIES: Final = frozenset({"boto3.client", "boto3.resource"})


def unresolved_client_annotation(value: str, node: ast.AST) -> str:
    """SDK-like annotations without an SDK binding justify review, never proof."""
    if value == "unproven:client":
        return value
    name = (
        node.id
        if isinstance(node, ast.Name)
        else node.attr
        if isinstance(node, ast.Attribute)
        else ""
    )
    candidate = (
        value.rsplit(".", 1)[-1]
        if value.startswith("module:")
        else name
        if not value
        else ""
    )
    return "unproven:client" if candidate in {"Daytona", "AsyncDaytona"} else ""


def foreign_constructor(module: str, name: str, index: ProjectIndex) -> str:
    """Preserve imported callable identity without claiming its result type."""
    if (
        not index.owns(module)
        and module.split(".")[0] not in {"daytona", "daytona_sdk"}
        and "daytona" in (module + "." + name).lower()
    ):
        return "unproven:client"
    return (
        "module:" + module + "." + name
        if not index.owns(module)
        and module.split(".")[0] not in {"daytona", "daytona_sdk"}
        else ""
    )


def external_call_kind(function: str, callee: str, index: ProjectIndex) -> str:
    """Only class-like names and known factories justify unrelated instances."""
    if function == "unproven:client":
        return function
    if not function.startswith("module:"):
        return ""
    path = function.removeprefix("module:")
    if index.owns(path) or path.split(".")[0] in {"daytona", "daytona_sdk"}:
        return ""
    if "daytona" in (path + "." + callee).lower():
        return "unproven:client"
    if (
        re.fullmatch(r"[A-Z][A-Za-z0-9]*", path.rsplit(".", 1)[-1])
        or path in _KNOWN_FACTORIES
    ):
        return "external-instance"
    return ""
