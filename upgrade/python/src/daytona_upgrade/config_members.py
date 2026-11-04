# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic config surface diagnostics and bounded factory provenance."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Final

import libcst as cst

from .config_literal_mappings import ast_mapping, ast_options

if TYPE_CHECKING:
    from .calls import CallContext
    from .config_origins import ConfigOrigins

MEMBERS: Final = frozenset(
    {
        "model_dump",
        "model_dump_json",
        "model_json_schema",
        "model_construct",
        "model_copy",
        "model_validate",
        "model_validate_json",
        "model_fields",
        "model_fields_set",
        "model_extra",
        "model_computed_fields",
        "model_post_init",
        "model_rebuild",
        "model_parametrized_name",
        "model_config",
        "dict",
        "json",
        "copy",
        "parse_obj",
        "parse_raw",
        "schema",
        "schema_json",
        "construct",
        "__fields__",
        "__fields_set__",
        "from_orm",
    }
)
COPIES: Final = frozenset({"model_copy", "copy"})
VALIDATORS: Final = frozenset({"model_validate", "parse_obj"})
FACTORIES: Final = (
    COPIES
    | VALIDATORS
    | {"construct", "model_construct", "model_validate_json", "parse_raw", "from_orm"}
)


def factory_kind(member: ast.Attribute, receiver: str) -> str:
    """A proven Pydantic factory produces another config, not an arbitrary value."""
    return (
        "config"
        if member.attr in FACTORIES and receiver in {"config", "type:config"}
        else ""
    )


def factory(node: ast.AST, origins: ConfigOrigins) -> bool:
    """Recognize a config-producing API without trusting its input payload."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and bool(factory_kind(node.func, origins.kind(node.func.value)))
        and not origins.uses.hooks
    )


def resolved_factory(node: ast.Call, origins: ConfigOrigins) -> bool:
    """Prove literal payloads only; dynamic mappings keep the existing refusal."""
    if not factory(node, origins) or not isinstance(node.func, ast.Attribute):
        return False
    member = node.func.attr
    receiver = node.func.value
    if member in COPIES and origins.kind(receiver) == "config":
        if not (
            isinstance(receiver, ast.Name)
            and origins.resolved_name(receiver)
            or isinstance(receiver, ast.Call)
            and origins.resolved_call(receiver)
        ):
            return False

    def dictionary(value: ast.expr) -> bool:
        entries = ast_mapping(value)
        return entries is not None and all(
            key not in {"api_url", "server_url"} or origins.uses.default(item)
            for key, item in entries.items()
        )

    options = ast_options(node.keywords)
    if options is None:
        return False
    if member in VALIDATORS:
        payload = node.args[0] if node.args else options.get("obj")
        return payload is not None and dictionary(payload)
    if member in COPIES:
        return not node.args and all(
            key != "update" or dictionary(value) for key, value in options.items()
        )
    if member in {"construct", "model_construct"}:
        return not node.args and all(
            key not in {"api_url", "server_url"} or origins.uses.default(value)
            for key, value in options.items()
        )
    # JSON validators can conceal URL keys, so keep them unresolved for now.
    return False


def report_member(context: CallContext, node: cst.Attribute) -> None:
    """Flag v0 BaseModel usage even for metadata and detached methods."""
    if (
        context.options.mode != "upgrade"
        or node.attr.value not in MEMBERS
        or context.kind(node.value) not in {"config", "type:config"}
    ):
        return
    member = node.attr.value
    receiver = cst.Module([]).code_for_node(node.value)
    hint = "review this Pydantic API manually"
    if member in {"model_dump", "dict"}:
        hint = f"use dataclasses.asdict({receiver})"
    elif member in COPIES:
        hint = f"use dataclasses.replace({receiver}, ...)"
    elif member in VALIDATORS | {"construct", "model_construct"}:
        hint = "use the DaytonaConfig(**values) constructor"
    message = f"SDK 2.x DaytonaConfig is a dataclass, not a Pydantic BaseModel; {hint}."
    context.item(node, "config-field", message)
    context.fixme(node, message)


def rewrite_config(
    context: CallContext, original: cst.Call, updated: cst.Call
) -> cst.Call:
    """Reuse constructor URL checks on literal copy/update/validation payloads."""
    from .config import ConfigCall
    from .config_factory_calls import FactoryCall

    if context.kind(original.func) == "type:config":
        return ConfigCall(context, original).rewrite(updated)
    if not isinstance(original.func, cst.Attribute):
        return updated
    member = original.func.attr.value
    if member not in FACTORIES or context.kind(original.func.value) not in {
        "config",
        "type:config",
    }:
        return updated
    if member in COPIES | VALIDATORS | {"construct", "model_construct"}:
        return FactoryCall(context, original).rewrite_payloads(updated)
    return updated
