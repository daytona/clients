# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Read-only preflight of explicitly loaded environment files."""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .environment import scan_environment
from .models import Item, TransformOptions
from .regular_files import read_environment_text

if TYPE_CHECKING:
    from .flow import SourceContext
    from .resolution import Resolver

DOTENV_REVIEW: Final = "Review dotenv load: the file it loads may set DAYTONA_API_URL, which SDK 2.x .v0 ignores."
SETTINGS_CLASSES: Final = frozenset(
    {
        "module:pydantic.BaseSettings",
        "module:pydantic_settings.BaseSettings",
        "settings-class",
    }
)


@dataclass(frozen=True)
class DotenvLoads:
    """Resolve both plausible working directories; never execute a loader."""

    options: TransformOptions
    context: SourceContext

    def scan(
        self, node: ast.AST, resolver: Resolver, env: dict[str, str]
    ) -> tuple[Item, ...]:
        inputs: list[ast.expr] = []
        items: list[Item] = []
        if isinstance(node, ast.Call):
            function = resolver.infer(node.func, env)
            if function in {"module:dotenv.load_dotenv", "module:dotenv.dotenv_values"}:
                inputs = node.args[:1] or [
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "dotenv_path"
                ]
                if (
                    not inputs
                    or isinstance(inputs[0], ast.Constant)
                    and inputs[0].value is None
                ):
                    inputs = []
                    items.extend(
                        self.read(
                            ast.copy_location(ast.Constant(".env"), node),
                            "dotenv-search:.env",
                        )
                    )
                if any(keyword.arg in {None, "stream"} for keyword in node.keywords):
                    inputs.append(ast.copy_location(ast.Name(id="@dynamic"), node))
            if function in {
                "module:pydantic_settings.SettingsConfigDict",
                "module:pydantic_settings.main.SettingsConfigDict",
            }:
                inputs = [
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "env_file"
                ]
                if any(keyword.arg is None for keyword in node.keywords):
                    inputs.append(ast.copy_location(ast.Name(id="@dynamic"), node))
            if function in SETTINGS_CLASSES:
                inputs = [
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "_env_file"
                ]
                if any(keyword.arg is None for keyword in node.keywords):
                    inputs.append(ast.copy_location(ast.Name(id="@dynamic"), node))
        if isinstance(node, ast.ClassDef) and any(
            resolver.infer(base, env) in SETTINGS_CLASSES for base in node.bases
        ):
            inputs = [
                statement.value
                for config in node.body
                if isinstance(config, ast.ClassDef) and config.name == "Config"
                for statement in config.body
                if isinstance(statement, (ast.Assign, ast.AnnAssign))
                and statement.value is not None
                and any(
                    isinstance(target, ast.Name) and target.id == "env_file"
                    for target in (
                        statement.targets
                        if isinstance(statement, ast.Assign)
                        else [statement.target]
                    )
                )
            ]
        return (
            *items,
            *(
                item
                for value in inputs
                for element in (
                    value.elts if isinstance(value, (ast.Tuple, ast.List)) else [value]
                )
                for item in self.read(element, resolver.infer(element, env))
            ),
        )

    def read(self, node: ast.expr, kind: str) -> tuple[Item, ...]:
        """Unknown, missing or unreadable files retain review rather than false safety."""
        if isinstance(node, ast.Constant) and node.value is None:
            return ()
        review = Item(
            file=self.context.file,
            line=node.lineno,
            column=node.col_offset + 1,
            kind="env-api-url",
            message=DOTENV_REVIEW,
        )
        base = self.context.root
        prefix, _, filename = kind.partition(":")
        if (
            base is None
            or prefix not in {"literal", "dotenv-search", "dotenv-search-uncertain"}
            or not filename
        ):
            return (review,)
        literal = Path(filename)
        source_directory = (base / self.context.file).parent
        candidates = sorted({base / literal, source_directory / literal})
        if prefix.startswith("dotenv-search"):
            candidates = []
            directory = source_directory
            while directory == base or base in directory.parents:
                candidates.append(directory / literal)
                if directory == base:
                    break
                directory = directory.parent
        items: list[Item] = [review] if prefix == "dotenv-search-uncertain" else []
        found = False
        for path in candidates:
            if not path.is_file():
                continue
            found = True
            try:
                source = read_environment_text(path)
            except (OSError, UnicodeError):
                items.append(review)
                continue
            relative = Path(os.path.relpath(path, base)).as_posix()
            items.extend(
                item
                for write in scan_environment(source, ".env")
                for item in replace(write, file=relative).items(
                    self.options.drop_api_url
                )
            )
        return tuple(items) if found else (review,)
