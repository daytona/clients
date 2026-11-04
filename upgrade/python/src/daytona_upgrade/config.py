# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Config field migration, including conservative keyword-splat preflight."""

from __future__ import annotations

from typing import TYPE_CHECKING

import libcst as cst

from .config_arguments import retain_argument_layout
from .config_messages import CUSTOM_URL, UNRESOLVED_CONFIG
from .url_writes import default_url

if TYPE_CHECKING:
    from .calls import CallContext


class ConfigCall:
    """Accumulate field edits while tracking user-owned unresolved mappings."""

    def __init__(self, context: CallContext, original: cst.Call) -> None:
        self.context = context
        self.original = original
        self.resolved = True
        self.dropped: list[cst.CSTNode] = []

    def rewrite(self, updated: cst.Call) -> cst.Call:
        args: list[cst.Arg] = []
        for old, new in zip(self.original.args, updated.args):
            if old.star == "**":
                args.append(
                    new.with_changes(value=self.dictionary(old.value, new.value))
                )
            elif not old.keyword or self.keep_field(old, old.keyword.value, old.value):
                args.append(new)
        if not self.resolved or (
            self.context.options.drop_api_url
            and self.context.position(self.original)
            in self.context.config_origins.preserved
        ):
            return self.original
        for node in self.dropped:
            self.context.item(
                node,
                "config-field",
                "Dropped non-literal API URL after --drop-api-url assertion.",
            )
        return retain_argument_layout(updated, args)

    def dictionary(
        self, original: cst.BaseExpression, updated: cst.BaseExpression
    ) -> cst.BaseExpression:
        if not isinstance(original, cst.Dict) or not isinstance(updated, cst.Dict):
            self.unknown(original)
            return updated
        last_keys = {
            element.key.evaluated_value: index
            for index, element in enumerate(original.elements)
            if isinstance(element, cst.DictElement)
            and isinstance(element.key, cst.SimpleString)
            and isinstance(element.key.evaluated_value, str)
        }
        elements: list[cst.BaseDictElement] = []
        for index, (old, new) in enumerate(zip(original.elements, updated.elements)):
            if isinstance(old, cst.StarredDictElement) and isinstance(
                new, cst.StarredDictElement
            ):
                elements.append(
                    new.with_changes(value=self.dictionary(old.value, new.value))
                )
                continue
            if isinstance(old, cst.DictElement):
                key = (
                    old.key.evaluated_value
                    if isinstance(old.key, cst.SimpleString)
                    else None
                )
                if isinstance(key, str):
                    if last_keys[key] != index:
                        # Removing the effective URL must not expose a shadowed one.
                        if self.context.mapping.config.get(key) != "url":
                            elements.append(new)
                        elif not isinstance(old.value, cst.SimpleString):
                            # Its evaluation may have effects even though its value loses.
                            self.unknown(old.value)
                        continue
                    if self.keep_field(old, key, old.value):
                        elements.append(new)
                    continue
                self.unknown(old.key)
            elements.append(new)
        return updated.with_changes(elements=elements)

    def unknown(self, node: cst.CSTNode) -> None:
        self.resolved = False
        asserted = self.context.options.drop_api_url
        self.context.item(
            node,
            "config-field" if asserted else "custom-api-url",
            "--drop-api-url asserts Cloud usage; review this unchanged call manually."
            if asserted
            else UNRESOLVED_CONFIG,
        )

    def keep_field(
        self, node: cst.CSTNode, key: str, value: cst.BaseExpression
    ) -> bool:
        action = self.context.mapping.config.get(key, "keep")
        if action == "url":
            shared = self.context.config_origins.shared_urls.get(
                self.context.position(self.original)
            )
            if shared:
                message = f"SDK 2.x refuses URL fields in the client config, but other code reads {shared}.{key}; give the client a config without {key} and keep the URL where it is read."
                self.context.item(self.original, "config-field", message)
                self.context.fixme(self.original, message)
            literal = (
                value.evaluated_value if isinstance(value, cst.SimpleString) else None
            )
            if default_url(value, self.context.mapping):
                return bool(shared)
            if literal is None and self.context.options.drop_api_url:
                if shared:
                    return True
                self.dropped.append(node)
                return False
            self.context.item(
                node,
                "custom-api-url",
                CUSTOM_URL if literal is not None else UNRESOLVED_CONFIG,
            )
        if action == "deprecated":
            self.context.item(
                node,
                "deprecated-target",
                "DaytonaConfig.target is deprecated; use the v0 per-call target instead.",
            )
        if action == "fixme":
            message = f"DaytonaConfig.{key} is not supported by SDK 2.x; review and remove it."
            self.context.item(node, "config-field", message)
            self.context.fixme(self.original, message)
        return True
