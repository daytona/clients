# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""LibCST codemod: receiver-proven rewrites with original-position reports."""

from __future__ import annotations

from collections.abc import Sequence

import libcst as cst
from libcst.metadata import (
    CodeRange,
    ParentNodeProvider,
    PositionProvider,
)

from .annotations import rewrite_string_annotation
from .attributes import rewrite_attribute
from .calls import rewrite_call
from .comments import leading_comments
from .config_members import report_member
from .config_origins import ConfigOrigins
from .double_rewrites import rewrite_double
from .export_checks import export_items
from .flow import Flow
from .imports import module_name, moved_module, split_import
from .member_mutations import attribute_write, call_write
from .models import ItemKind, TransformOptions
from .source_transform import transform_source as transform_source
from .suggestions import Guidance, guidance
from .url_writes import rewrite_setattr, rewrite_statement
from .wildcard_imports import paired_wildcard


class Upgrade(cst.CSTTransformer):
    """Mutable visitor accumulating edits and manual-review diagnostics."""

    METADATA_DEPENDENCIES = (PositionProvider, ParentNodeProvider)

    def __init__(self, flow: Flow, options: TransformOptions, newline: str) -> None:
        self.flow = flow
        self.options = options
        self.newline = newline
        self.mapping = options.mapping
        self.config_origins = ConfigOrigins(flow)
        generated = (
            guidance(flow, options.mapping)
            if options.mode == "suggest-v2"
            else Guidance({}, tuple(export_items(flow)))
        )
        self.comments = generated.comments
        self.items = list(generated.items)
        self.constructors: dict[tuple[int, int], cst.Call] = {}
        self.double_aliases: dict[cst.BaseSmallStatement, str] = {}
        self.sdk_mock_found = False

    def position(self, node: cst.CSTNode) -> tuple[int, int]:
        location = self.get_metadata(PositionProvider, node)
        # LibCST's stub includes its default sentinel even without a default argument.
        assert isinstance(location, CodeRange)
        position = location.start
        # AST columns count UTF-8 bytes; LibCST columns count Unicode characters.
        line = self.flow.text.lines[position.line - 1]
        return position.line, len(line[: position.column].encode("utf-8"))

    def kind(self, node: cst.CSTNode) -> str:
        location = self.get_metadata(PositionProvider, node)
        assert isinstance(location, CodeRange)
        end = location.end
        line = self.flow.text.lines[end.line - 1]
        return self.flow.kinds.get(
            (*self.position(node), end.line, len(line[: end.column].encode("utf-8"))),
            "",
        )

    def item(self, node: cst.CSTNode, kind: ItemKind, message: str) -> None:
        line, column = self.position(node)
        self.items.append(
            {
                "file": self.flow.context.file,
                "line": line,
                "column": column + 1,
                "kind": kind,
                "message": message,
            }
        )

    def mock_linked(self, node: cst.CSTNode) -> bool:
        return self.position(node) in self.flow.mock_links

    def parent(self, node: cst.CSTNode) -> cst.CSTNode:
        parent = self.get_metadata(ParentNodeProvider, node)
        assert isinstance(parent, cst.CSTNode)
        return parent

    def fixme(self, node: cst.CSTNode, message: str) -> None:
        line, column = self.position(node)
        # Find the owning statement rather than inserting into a multiline expression.
        statement = self.flow.statements.get((line, column), line)
        self.comments.setdefault(statement, []).append(
            "FIXME(daytona-upgrade): " + message
        )

    def leave_Attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute
    ) -> cst.BaseExpression:
        parent = self.metadata[ParentNodeProvider][original_node]
        while isinstance(
            parent,
            (cst.Element, cst.StarredElement, cst.Tuple, cst.List, cst.AssignTarget),
        ):
            parent = self.metadata[ParentNodeProvider][parent]
        assert isinstance(parent, cst.CSTNode)
        if self.options.mode == "upgrade" and attribute_write(
            self, original_node, parent
        ):
            return updated_node
        report_member(self, original_node)
        return rewrite_attribute(self, original_node, updated_node)

    def visit_Import(self, node: cst.Import) -> bool:
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        return False

    def leave_SimpleString(
        self, original_node: cst.SimpleString, updated_node: cst.SimpleString
    ) -> cst.BaseExpression:
        return rewrite_string_annotation(self, original_node, updated_node)

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        if (
            self.options.mode == "upgrade"
            and original_node.value == "daytona_sdk"
            and self.kind(original_node) == "module:daytona_sdk"
        ):
            return updated_node.with_changes(value="daytona")
        return updated_node

    def leave_Import(
        self, original_node: cst.Import, updated_node: cst.Import
    ) -> cst.Import:
        if self.options.mode != "upgrade":
            return updated_node
        names: list[cst.ImportAlias] = []
        for alias in updated_node.names:
            name = module_name(alias.name)
            renamed = moved_module(name, self.mapping, self.flow.context.index)
            if renamed != name and name not in self.mapping.modules:
                self.item(
                    original_node,
                    "private-import",
                    "Private Daytona import moved under daytona.v0; review this import.",
                )
            names.append(alias.with_changes(name=cst.parse_expression(renamed)))
        return updated_node.with_changes(names=names)

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom | cst.FlattenSentinel[cst.BaseSmallStatement]:
        # A semicolon-delimited line or one-line suite cannot expand into full lines.
        # Ordinary standalone imports are split by leave_SimpleStatementLine below.
        if self.options.mode != "upgrade" or original_node.relative:
            return updated_node
        if paired_wildcard(original_node, self.parent):
            return updated_node
        parent = self.metadata[ParentNodeProvider][original_node]
        if isinstance(parent, cst.SimpleStatementLine) and len(parent.body) == 1:
            return updated_node
        name = module_name(updated_node.module)
        if name.split(".")[0] in self.mapping.modules:
            return cst.FlattenSentinel(
                split_import(updated_node, self.mapping, self.flow.context.index)
            )
        return updated_node

    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if self.options.mode != "upgrade":
            return updated_node
        updated = call_write(self, original_node, updated_node)
        updated = rewrite_call(self, original_node, updated)
        result = (
            rewrite_setattr(self, original_node, updated)
            if self.flow.found
            else updated
        )
        position = self.position(original_node)
        if position in self.flow.constructor_envs and isinstance(result, cst.Call):
            self.constructors[position] = result
        return result

    def leave_EmptyLine(
        self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine
    ) -> cst.EmptyLine | cst.RemovalSentinel:
        if (
            self.options.mode != "upgrade"
            and original_node.comment
            and original_node.comment.value.startswith("# daytona-v2:")
        ):
            return cst.RemoveFromParent()
        return updated_node

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,
        updated_node: cst.SimpleStatementLine,
    ) -> cst.SimpleStatementLine | cst.FlattenSentinel[cst.BaseStatement]:
        statements: list[cst.BaseStatement] = []
        if (
            self.options.mode == "upgrade"
            and len(updated_node.body) == 1
            and isinstance(updated_node.body[0], cst.ImportFrom)
            and not updated_node.body[0].relative
            and not self.flow.context.index.owns(
                module_name(updated_node.body[0].module)
            )
        ):
            node = updated_node.body[0]
            name = module_name(node.module)
            if name.startswith(
                tuple(old + "." for old in self.mapping.modules)
            ) and not name.startswith(("daytona.v0", "daytona.v2")):
                self.item(
                    original_node,
                    "private-import",
                    "Private Daytona import moved under daytona.v0; review this import.",
                )
            if name.split(".")[0] in self.mapping.modules and not paired_wildcard(
                original_node.body[0], self.parent
            ):
                imports = split_import(node, self.mapping, self.flow.context.index)
                statements = [
                    updated_node.with_changes(
                        body=[item],
                        leading_lines=updated_node.leading_lines if i == 0 else (),
                    )
                    for i, item in enumerate(imports)
                ]
        leading = self.leading(original_node, updated_node.leading_lines)
        if statements:
            first = statements[0]
            if isinstance(first, cst.SimpleStatementLine):
                statements[0] = first.with_changes(leading_lines=leading)
            return cst.FlattenSentinel(statements)
        return updated_node.with_changes(leading_lines=leading)

    def leading(
        self, node: cst.CSTNode, existing: Sequence[cst.EmptyLine]
    ) -> list[cst.EmptyLine]:
        line, _ = self.position(node)
        messages = self.comments.get(line, [])
        prefix = "\n".join(
            self.flow.text.lines[max(0, line - len(messages) - 3) : line - 1]
        )
        return leading_comments(existing, messages, (self.options.mode, prefix))

    def on_leave(
        self, original_node: cst.CSTNode, updated_node: cst.CSTNode
    ) -> cst.CSTNode | cst.RemovalSentinel | cst.FlattenSentinel[cst.CSTNode]:
        result = super().on_leave(original_node, updated_node)
        if (
            self.options.mode == "upgrade"
            and self.flow.found
            and isinstance(original_node, cst.BaseSmallStatement)
            and isinstance(result, cst.BaseSmallStatement)
        ):
            result = rewrite_statement(self, original_node, result)
        if isinstance(result, (cst.BaseCompoundStatement, cst.MatchCase)):
            result = result.with_changes(
                leading_lines=self.leading(original_node, result.leading_lines)
            )
        return (
            rewrite_double(self, original_node, result)
            if isinstance(result, cst.CSTNode)
            else result
        )
