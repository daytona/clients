# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Check effective Pydantic factory payloads without losing literal evaluation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import libcst as cst

from .config import ConfigCall
from .config_literal_mappings import literal_elements

if TYPE_CHECKING:
    from .calls import CallContext


class FactoryCall(ConfigCall):
    """Accumulate payload edits only after resolving literal merge precedence."""

    def __init__(self, context: CallContext, original: cst.Call) -> None:
        super().__init__(context, original)
        assert isinstance(original.func, cst.Attribute)
        member = original.func.attr.value
        self.payload = (
            "update"
            if member in {"model_copy", "copy"}
            else ""
            if member in {"model_construct", "construct"}
            else "obj"
        )
        self.selected: set[int] | None = None

    def manual(self, node: cst.CSTNode) -> None:
        """Preserve evaluations or duplicate-key errors instead of inventing code."""
        self.resolved = False
        self.context.item(
            node,
            "config-field",
            "Literal merge needs manual review; the unchanged call preserves overwritten evaluations and duplicate keyword errors.",
        )

    def dictionary(
        self, original: cst.BaseExpression, updated: cst.BaseExpression
    ) -> cst.BaseExpression:
        entries = literal_elements(original)
        if entries is None:
            return super().dictionary(original, updated)
        effective = {
            entry.key.evaluated_value: entry
            for entry in entries
            if isinstance(entry.key, cst.SimpleString)
        }
        dropped: set[int] = set()
        for entry in entries:
            assert isinstance(entry.key, cst.SimpleString)
            key = entry.key.evaluated_value
            assert isinstance(key, str)
            if effective[key] is not entry:
                if self.context.mapping.config.get(key) == "url":
                    if isinstance(entry.value, cst.SimpleString) or (
                        isinstance(entry.value, cst.Name)
                        and entry.value.value == "None"
                    ):
                        dropped.add(id(entry))
                    else:
                        # Even a shadowed expression is evaluated. Keep the whole
                        # payload manual rather than exposing it or deleting effects.
                        self.manual(entry)
            elif not self.keep_field(entry, key, entry.value):
                dropped.add(id(entry))

        def remove(
            old: cst.BaseExpression, new: cst.BaseExpression
        ) -> cst.BaseExpression:
            assert isinstance(old, cst.Dict) and isinstance(new, cst.Dict)
            elements: list[cst.BaseDictElement] = []
            for before, after in zip(old.elements, new.elements):
                if id(before) in dropped:
                    continue
                if isinstance(before, cst.StarredDictElement) and isinstance(
                    after, cst.StarredDictElement
                ):
                    after = after.with_changes(value=remove(before.value, after.value))
                elements.append(after)
            return new.with_changes(elements=elements)

        return remove(original, updated)

    def keywords(
        self, original: cst.BaseExpression, updated: cst.BaseExpression
    ) -> cst.BaseExpression:
        """Descend only to effective update/obj values, never shadowed payloads."""
        if not isinstance(original, cst.Dict) or not isinstance(updated, cst.Dict):
            self.unknown(original)
            return updated
        elements: list[cst.BaseDictElement] = []
        for old, new in zip(original.elements, updated.elements):
            if isinstance(old, cst.StarredDictElement) and isinstance(
                new, cst.StarredDictElement
            ):
                new = new.with_changes(value=self.keywords(old.value, new.value))
            elif isinstance(old, cst.DictElement) and isinstance(new, cst.DictElement):
                if not isinstance(old.key, cst.SimpleString):
                    self.unknown(old.key)
                elif old.key.evaluated_value == self.payload and (
                    self.selected is None or id(old.value) in self.selected
                ):
                    new = new.with_changes(value=self.dictionary(old.value, new.value))
            elements.append(new)
        return updated.with_changes(elements=elements)

    def rewrite_payloads(self, updated: cst.Call) -> cst.Call:
        options: dict[str, cst.BaseExpression] = {}
        duplicate = False
        complete = True
        for arg in self.original.args:
            entries: dict[str, cst.BaseExpression] = {}
            if arg.keyword is not None:
                entries[arg.keyword.value] = arg.value
            elif arg.star == "**":
                expanded = literal_elements(arg.value)
                if expanded is None:
                    complete = False
                else:
                    entries = {
                        entry.key.evaluated_value: entry.value
                        for entry in expanded
                        if isinstance(entry.key, cst.SimpleString)
                        and isinstance(entry.key.evaluated_value, str)
                    }
            duplicate |= bool(options.keys() & entries.keys())
            options.update(entries)
        self.selected = {id(value) for value in options.values()} if complete else None
        if not self.payload:
            if duplicate and complete:
                for key, value in options.items():
                    self.keep_field(value, key, value)
                self.manual(self.original)
                return updated
            return self.rewrite(updated)
        args: list[cst.Arg] = []
        for index, (old, new) in enumerate(zip(self.original.args, updated.args)):
            if old.star == "**":
                new = new.with_changes(value=self.keywords(old.value, new.value))
            elif (
                old.star
                or (
                    old.keyword is not None
                    and old.keyword.value == self.payload
                    and (self.selected is None or id(old.value) in self.selected)
                )
                or (self.payload == "obj" and index == 0 and old.keyword is None)
            ):
                new = new.with_changes(value=self.dictionary(old.value, new.value))
            args.append(new)
        if duplicate:
            self.manual(self.original)
        return updated.with_changes(args=args) if self.resolved else updated
