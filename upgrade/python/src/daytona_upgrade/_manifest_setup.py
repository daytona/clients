# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Locate static setuptools dependency strings without executing setup.py."""

from __future__ import annotations

from collections.abc import Callable

import libcst as cst
from libcst.metadata import (
    ExpressionContext,
    ExpressionContextProvider,
    MetadataWrapper,
    ParentNodeProvider,
    PositionProvider,
)

from ._setup_values import SetupValue, setup_value
from ._setup_writes import Writes, mutation_name
from .python_floor import PYTHON_REVIEW, needs_python_review


class _Dependencies(cst.CSTVisitor):
    """Accumulate call-site literals; ambiguous compound writes remain manual."""

    def __init__(self, wrapper: MetadataWrapper) -> None:
        parents = wrapper.resolve(ParentNodeProvider)
        self.parents = parents
        # Annotation-only targets are marked STORE by LibCST but do not bind a value.
        self.contexts = {
            node: context
            for node, context in wrapper.resolve(ExpressionContextProvider).items()
            if not (
                isinstance(parent := parents.get(node), cst.AnnAssign)
                and parent.target is node
                and parent.value is None
            )
        }
        self.positions = wrapper.resolve(PositionProvider)
        self.assignments: dict[str, SetupValue | None] = {}
        self.literals: list[cst.SimpleString] = []
        self.runtime_literals: list[cst.SimpleString] = []
        self.manual: list[tuple[int, str]] = []
        self.blocked: list[set[str]] = []
        self.setup_names = {"setup"}
        self.modules = {"setuptools", "distutils.core"}

    def on_visit(self, node: cst.CSTNode) -> bool:
        name = mutation_name(node, self.contexts)
        if name:
            self.invalidate_container(name)
        if isinstance(node, cst.BaseCompoundStatement):
            writes = Writes(self.contexts)
            node.visit(writes)
            names = writes.names
            if isinstance(node, cst.FunctionDef):
                # Deferred bodies cannot rely on definition-time globals.
                names.update(self.assignments)
            self.blocked.append(names)
            for name in names:
                self.invalidate_container(name)
                self.assignments[name] = None
        return super().on_visit(node)

    def invalidate_container(self, name: str) -> None:
        """Drop every alias of the same saved container proof on mutation."""
        strings = self.assignments.get(name)
        if strings is not None:
            for alias, value in self.assignments.items():
                if value is strings:
                    self.assignments[alias] = None

    def on_leave(self, original_node: cst.CSTNode) -> None:
        super().on_leave(original_node)
        if isinstance(original_node, cst.BaseCompoundStatement):
            for name in self.blocked.pop():
                self.assignments[name] = None

    def visit_Name(self, node: cst.Name) -> None:
        if self.contexts.get(node) in (ExpressionContext.STORE, ExpressionContext.DEL):
            # Simple assignments restore their resolved value on leave; other
            # writes (augmentation, deletion, walrus) cannot retain old proofs.
            self.assignments[node.value] = None

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if (
            node.module is None
            or cst.Module([]).code_for_node(node.module) not in self.modules
        ):
            return
        if isinstance(node.names, cst.ImportStar):
            return
        for alias in node.names:
            if isinstance(alias.name, cst.Name) and alias.name.value == "setup":
                name = alias.asname.name if alias.asname else alias.name
                if isinstance(name, cst.Name):
                    self.setup_names.add(name.value)

    def visit_Import(self, node: cst.Import) -> None:
        for alias in node.names:
            if (
                cst.Module([]).code_for_node(alias.name) in self.modules
                and alias.asname
                and isinstance(alias.asname.name, cst.Name)
            ):
                self.modules.add(alias.asname.name.value)

    def leave_Assign(self, original_node: cst.Assign) -> None:
        strings = setup_value(original_node.value, self.assignments)
        for target in original_node.targets:
            if isinstance(target.target, cst.Name):
                name = target.target.value
                self.assignments[name] = (
                    None if any(name in names for names in self.blocked) else strings
                )

    def leave_AnnAssign(self, original_node: cst.AnnAssign) -> None:
        if (
            isinstance(original_node.target, cst.Name)
            and original_node.value is not None
        ):
            name = original_node.target.value
            self.assignments[name] = (
                None
                if any(name in names for names in self.blocked)
                else setup_value(original_node.value, self.assignments)
            )

    def visit_Call(self, node: cst.Call) -> None:
        function = cst.Module([]).code_for_node(node.func)
        if function not in self.setup_names and function not in {
            module + ".setup" for module in self.modules
        }:
            for argument in node.args:
                if argument.star != "**" and isinstance(argument.value, cst.Name):
                    name = argument.value.value
                    value = self.assignments.get(name)
                    if value is not None and value.floors is not None:
                        self.invalidate_container(name)
            return
        for argument in node.args:
            if argument.star == "**":
                value = setup_value(argument.value, self.assignments)
                if (
                    value is None
                    or value.floors is None
                    or any(
                        floor is None or needs_python_review(floor)
                        for floor in value.floors
                    )
                ):
                    self.manual.append(
                        (self.positions[argument.value].start.line, PYTHON_REVIEW)
                    )
            if argument.keyword and argument.keyword.value == "python_requires":
                strings = self.strings(argument.value)
                constraint = (
                    strings[0].evaluated_value
                    if strings is not None and len(strings) == 1
                    else None
                )
                if not isinstance(constraint, str) or needs_python_review(constraint):
                    self.manual.append(
                        (self.positions[argument.value].start.line, PYTHON_REVIEW)
                    )
            if argument.keyword and argument.keyword.value in {
                "install_requires",
                "extras_require",
                "tests_require",
                "setup_requires",
            }:
                strings = self.strings(argument.value)
                if strings is None:
                    self.manual.append(
                        (
                            self.positions[argument.value].start.line,
                            "Review this setup dependency expression manually; its runtime binding is uncertain and was preserved.",
                        )
                    )
                else:
                    self.literals.extend(strings)
                    # Only standalone module-level setup calls are unconditional.
                    parent = self.parents.get(node)
                    line = self.parents.get(parent) if parent is not None else None
                    if (
                        argument.keyword.value == "install_requires"
                        and isinstance(parent, cst.Expr)
                        and isinstance(line, cst.SimpleStatementLine)
                        and isinstance(self.parents.get(line), cst.Module)
                    ):
                        self.runtime_literals.extend(strings)

    def strings(
        self, expression: cst.BaseExpression
    ) -> tuple[cst.SimpleString, ...] | None:
        value = setup_value(expression, self.assignments)
        return value.strings if value is not None else None


class _Rewrite(cst.CSTTransformer):
    def __init__(self, replacements: dict[int, str]) -> None:
        self.replacements = replacements

    def leave_SimpleString(
        self, original_node: cst.SimpleString, updated_node: cst.SimpleString
    ) -> cst.SimpleString:
        replacement = self.replacements.get(id(original_node))
        if replacement is None:
            return updated_node
        quote = original_node.quote
        prefix = original_node.prefix
        escaped = replacement
        if "r" not in prefix.lower():
            escaped = (
                replacement.replace("\\", "\\\\")
                .replace(quote[0], "\\" + quote[0])
                .replace("\n", "\\n")
                .replace("\r", "\\r")
            )
        return updated_node.with_changes(value=prefix + quote + escaped + quote)


def runtime_setup_requirements(source: str) -> tuple[str, ...]:
    """Read static unconditional install_requires without executing setup.py."""
    wrapper = MetadataWrapper(cst.parse_module(source))
    dependencies = _Dependencies(wrapper)
    wrapper.visit(dependencies)
    return tuple(
        value
        for string in dependencies.runtime_literals
        if isinstance(value := string.evaluated_value, str)
    )


def rewrite_setup(
    source: str,
    rewrite: Callable[[str], str],
    manual: Callable[[int, str], None] | None = None,
) -> str:
    """Rewrite only statically discoverable setuptools requirement literals."""
    wrapper = MetadataWrapper(cst.parse_module(source))
    module = wrapper.module
    dependencies = _Dependencies(wrapper)
    wrapper.visit(dependencies)
    if manual is not None:
        for line, message in dependencies.manual:
            manual(line, message)
    replacements: dict[int, str] = {}
    for string in dependencies.literals:
        value = string.evaluated_value
        if isinstance(value, str) and id(string) not in replacements:
            target = rewrite(value)
            if value != target:
                replacements[id(string)] = target
    return module.visit(_Rewrite(replacements)).code
