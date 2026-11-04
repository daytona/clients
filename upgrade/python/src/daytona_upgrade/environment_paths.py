# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Literal environment paths without evaluating project Python."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .call_proofs import CallProofs
from .resolution import Resolver


@dataclass(frozen=True)
class EnvironmentResolver(Resolver):
    """Keep path/literal provenance isolated from ordinary receiver proofs."""

    source_path: Path | None = None

    def expression_kind(self, node: ast.AST, env: dict[str, str]) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return "literal:" + node.value
        if not isinstance(node, ast.Call) and not (
            isinstance(node, ast.Name) and node.id in env
        ):
            path = environment_path(
                node, lambda value: self.infer(value, env), self.source_path
            )
            if path is not None:
                return "literal:" + path
        return super().expression_kind(node, env)

    def call_kind(self, node: ast.Call, env: dict[str, str]) -> str:
        # Share callee inference with ordinary proofs: repeating it here multiplies
        # recursive work at every link of a non-path call chain.
        function = self.infer(node.func, env)
        if function == "module:dotenv.find_dotenv":
            return dotenv_search_kind(node, lambda value: self.infer(value, env))
        path = environment_call_path(
            node, function, lambda value: self.infer(value, env)
        )
        if path is not None:
            return "literal:" + path
        if function == "module:dotenv.dotenv_values":
            return "dotenv-values"
        return CallProofs(self, env).resolve(node, function)

    def imported(self, module: str, name: str) -> str:
        if self.index.owns(module):
            return super().imported(module, name)
        if module == "os" and name in {"environ", "putenv"}:
            return "module:os." + name
        if module in {"dotenv", "dotenv.main"} and name in {
            "load_dotenv",
            "dotenv_values",
            "find_dotenv",
        }:
            return "module:dotenv." + name
        if (module, name) in {
            ("pathlib", "Path"),
            ("os", "path"),
            ("os.path", "join"),
            ("os.path", "dirname"),
            ("pydantic", "BaseSettings"),
            ("pydantic_settings", "BaseSettings"),
            ("pydantic_settings", "SettingsConfigDict"),
            ("pydantic_settings.main", "SettingsConfigDict"),
        }:
            return "module:" + module + "." + name
        return super().imported(module, name)


def environment_path(
    node: ast.AST, infer: Callable[[ast.AST], str], source: Path | None
) -> str | None:
    """Resolve bounded pathlib/os.path operations using binding-proven callees."""
    if isinstance(node, ast.Name) and node.id == "__file__" and source is not None:
        return str(source)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        value = infer(node.value)
        if value.startswith("literal:"):
            return str(Path(value[8:]).parent)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = infer(node.left), infer(node.right)
        if left.startswith("literal:") and right.startswith("literal:"):
            return str(Path(left[8:]) / right[8:])
    return None


def environment_call_path(
    node: ast.Call, function: str, infer: Callable[[ast.AST], str]
) -> str | None:
    """Resolve path calls using the callee proof shared with ordinary call handling."""
    if function in {"module:pathlib.Path", "module:os.path.join"}:
        values = [infer(argument) for argument in node.args]
        if (
            values
            and not node.keywords
            and all(value.startswith("literal:") for value in values)
        ):
            return str(
                Path(values[0][8:]).joinpath(*(value[8:] for value in values[1:]))
            )
    if function == "module:os.path.dirname" and len(node.args) == 1:
        value = infer(node.args[0])
        if value.startswith("literal:") and not node.keywords:
            return str(Path(value[8:]).parent)
    return None


def dotenv_search_kind(node: ast.Call, infer: Callable[[ast.AST], str]) -> str:
    """Keep upward-search provenance through aliases, including uncertain CWD inputs."""
    keywords = {keyword.arg: keyword.value for keyword in node.keywords}
    filename = (
        node.args[0] if node.args else keywords.get("filename", ast.Constant(".env"))
    )
    value = infer(filename)
    usecwd = (
        node.args[2]
        if len(node.args) > 2
        else keywords.get("usecwd", ast.Constant(False))
    )
    certain = (
        None not in keywords
        and isinstance(usecwd, ast.Constant)
        and usecwd.value is False
        and value.startswith("literal:")
    )
    prefix = "dotenv-search:" if certain else "dotenv-search-uncertain:"
    return prefix + (value[8:] if value.startswith("literal:") else "")
