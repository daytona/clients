# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Apply source-position-certified double shape changes without replacing identity."""

from __future__ import annotations

from typing import Protocol

import libcst as cst

from .attributes import AttributeContext


class DoubleContext(AttributeContext, Protocol):
    newline: str
    double_aliases: dict[cst.BaseSmallStatement, str]


def rewrite_double(
    context: DoubleContext, original: cst.CSTNode, updated: cst.CSTNode
) -> cst.CSTNode | cst.FlattenSentinel[cst.CSTNode]:
    if context.options.mode != "upgrade" or not isinstance(
        original, (cst.Call, cst.ClassDef, cst.Assign, cst.SimpleStatementLine)
    ):
        return updated
    kind = context.kind(original)
    if kind == "local-double:namespace" and isinstance(updated, cst.Call):
        return cst.Call(updated.func, [cst.Arg(updated, keyword=cst.Name("v0"))])
    if kind == "local-double:class" and isinstance(updated, cst.ClassDef):
        body = updated.body
        if isinstance(body, cst.SimpleStatementSuite):
            body = cst.IndentedBlock(
                body=[cst.SimpleStatementLine(body=body.body)],
                header=body.trailing_whitespace,
            )
        property_ = cst.parse_statement(
            "@property\ndef v0(self):\n    return self\n".replace("\n", context.newline)
        )
        return updated.with_changes(
            body=body.with_changes(body=[*body.body, property_])
        )
    if kind.startswith("local-double:module-alias:") and isinstance(
        updated, cst.Assign
    ):
        context.double_aliases[updated] = kind.removeprefix(
            "local-double:module-alias:"
        )
    if isinstance(updated, cst.SimpleStatementLine) and any(
        statement in context.double_aliases for statement in updated.body
    ):
        statements: list[cst.BaseStatement] = []
        for statement in updated.body:
            statements.append(
                updated.with_changes(
                    body=[statement],
                    leading_lines=updated.leading_lines if not statements else (),
                )
            )
            if name := context.double_aliases.pop(statement, ""):
                statements.append(
                    cst.parse_statement(f"{name}.v0 = {name}{context.newline}")
                )
        return cst.FlattenSentinel(statements)
    return updated
