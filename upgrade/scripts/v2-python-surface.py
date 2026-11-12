#!/usr/bin/env python3
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# ─── How to run ───
# python3 v2-python-surface.py SDKROOT < mapping.json
"""Bind mapping calls to handwritten SDK receiver chains without importing it."""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict

Json = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
Function = ast.FunctionDef | ast.AsyncFunctionDef
EXCLUDED: Final = frozenset({"_sync", "_gen", "gen", "generated", "api"})


class Result(TypedDict):
    count: int
    problems: list[str]


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    receiver: str
    form: str


@dataclass(frozen=True, slots=True)
class Receiver:
    names: tuple[str, ...]
    producers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Mapping:
    entries: tuple[Entry, ...]
    receivers: dict[str, Receiver]


@dataclass(frozen=True, slots=True)
class Bound:
    symbol: str
    method: Function | None = None


@dataclass(frozen=True, slots=True)
class Unresolved(Exception):
    message: str


def parse_mapping(value: Json) -> Mapping:
    """Parse the consumed JSON boundary, ignoring unrelated mapping fields."""
    match value:
        case {"suggestions": list(suggestions)}:
            match value.get("receivers", {}):
                case dict(receivers):
                    pass
                case _:
                    raise Unresolved("invalid receivers metadata")
            entries: list[Entry] = []
            parsed: dict[str, Receiver] = {}
            for row in suggestions:
                match row:
                    case {"id": str(id_), "v2": {"py": form}} if isinstance(form, str) or form is None:
                        match row.get("receiver", ""):
                            case str(receiver):
                                if form is not None:
                                    entries.append(Entry(id_, receiver, form))
                            case _:
                                raise Unresolved("invalid suggestion receiver")
                    case _:
                        raise Unresolved("invalid Python suggestion")
            for key, row in receivers.items():
                match row:
                    case {"py": {"types": list(names)}}:
                        producers = row.get("producedBy", [])
                        match producers:
                            case list(items) if all(isinstance(item, str) for item in items + names):
                                parsed[key] = Receiver(tuple(str(n) for n in names), tuple(str(p) for p in items))
                            case _:
                                raise Unresolved("invalid receiver producers")
                    case _:
                        raise Unresolved("invalid Python receiver")
            return Mapping(tuple(entries), parsed)
        case _:
            raise Unresolved("expected mapping suggestions")


def form_tree(form: str) -> ast.Module:
    return ast.parse(re.sub(r"\{(client|sandbox)\}", r"\1", form))


class Surface:
    """Mutable syntax index; only receiver-qualified declarations are callable."""

    def __init__(self, root: Path) -> None:
        self.classes: dict[str, ast.ClassDef] = {}
        self.imports: dict[str, dict[str, str]] = {}
        self.aliases: dict[str, ast.expr] = {}
        source = root / "sdk-python/src"
        paths = sorted(p for p in (source / "daytona/v2").rglob("*.py") if not EXCLUDED.intersection(p.relative_to(source).parts))
        for path in [source / "daytona/_client.py", *paths]:
            parts = list(path.relative_to(source).with_suffix("").parts)
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            package = module if path.stem == "__init__" else module.rpartition(".")[0]
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = self.imports.setdefault(module, {})
            for node in ast.walk(tree):
                match node:
                    case ast.ImportFrom(module=target, level=level, names=names):
                        prefix = package.split(".")[:len(package.split(".")) - level + 1] if level else []
                        base = ".".join([*prefix, *([target] if target else [])])
                        for name in names:
                            imports[name.asname or name.name] = f"{base}.{name.name}"
                    case ast.Import(names=names):
                        for name in names:
                            imports[name.asname or name.name.split(".")[0]] = name.name if name.asname else name.name.split(".")[0]
                    case _:
                        continue
            for node in tree.body:
                match node:
                    case ast.ClassDef(name=name):
                        self.classes[f"{module}.{name}"] = node
                    case ast.Assign(targets=[ast.Name(id=name)], value=value) | ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value):
                        self.aliases[f"{module}.{name}"] = value
                    case _:
                        continue

    def annotation(self, node: ast.expr | None, module: str, owner: str = "", depth: int = 0) -> Bound:
        if depth > 20:
            raise Unresolved("cyclic type alias")
        match node:
            case ast.Constant(value=str(text)):
                return self.annotation(ast.parse(text, mode="eval").body, module, owner, depth + 1)
            case ast.Name(id="Self"):
                return Bound(owner)
            case ast.Name(id=name):
                symbol = self.imports.get(module, {}).get(name, f"{module}.{name}")
            case ast.Attribute():
                symbol = ast.unparse(node)
                head, _, tail = symbol.partition(".")
                symbol = self.imports.get(module, {}).get(head, head) + "." + tail
            case ast.Subscript(value=ast.Name(id="Optional" | "Annotated"), slice=inner):
                return self.annotation(inner.elts[0] if isinstance(inner, ast.Tuple) else inner, module, owner, depth + 1)
            case ast.BinOp(left=left, op=ast.BitOr(), right=ast.Constant(value=None)):
                return self.annotation(left, module, owner, depth + 1)
            case _:
                raise Unresolved("unresolved return annotation")
        # Sync classes are generated projections of the async authoring classes.
        if symbol == "daytona.v2._sync.Daytona":
            symbol = "daytona.v2.daytona.AsyncDaytona"
        if symbol in self.classes:
            return Bound(symbol)
        if symbol in self.aliases:
            return self.annotation(self.aliases[symbol], symbol.rpartition(".")[0], owner, depth + 1)
        raise Unresolved(f"unresolved type {symbol}")

    def member(self, owner: Bound, name: str, depth: int = 0) -> Bound:
        if depth > 20 or owner.method is not None or owner.symbol not in self.classes:
            raise Unresolved(f"unresolved receiver for {name}")
        cls = self.classes[owner.symbol]
        module = owner.symbol.rpartition(".")[0]
        for node in reversed(cls.body):
            match node:
                case ast.FunctionDef(name=method) | ast.AsyncFunctionDef(name=method) if method == name:
                    if any(ast.unparse(d).split(".")[-1] == "property" for d in node.decorator_list):
                        return self.annotation(node.returns, module, owner.symbol)
                    return Bound(owner.symbol, node)
                case ast.AnnAssign(target=ast.Name(id=field), annotation=annotation) if field == name:
                    return self.annotation(annotation, module, owner.symbol)
                case _:
                    continue
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
                for assignment in ast.walk(node):
                    match assignment:
                        case ast.Assign(targets=[ast.Attribute(value=ast.Name(id="self"), attr=field)], value=value) if field == name:
                            return self.expression(value, {"self": owner}, module)
                        case ast.AnnAssign(target=ast.Attribute(value=ast.Name(id="self"), attr=field), annotation=annotation) if field == name:
                            return self.annotation(annotation, module, owner.symbol)
                        case _:
                            continue
        for base in cls.bases:
            try:
                return self.member(self.annotation(base, module), name, depth + 1)
            except Unresolved:
                continue
        raise Unresolved(f"no member {name} on {cls.name}")

    def expression(self, node: ast.expr, locals_: dict[str, Bound], module: str = "") -> Bound:
        match node:
            case ast.Name(id=name) if name in locals_:
                return locals_[name]
            case ast.Name() if module:
                return self.annotation(node, module)
            case ast.Attribute(value=value, attr=name):
                return self.member(self.expression(value, locals_, module), name)
            case ast.Await(value=value):
                return self.expression(value, locals_, module)
            case ast.Call(func=func):
                bound = self.expression(func, locals_, module)
                if bound.method is None:
                    return bound
                return self.annotation(bound.method.returns, bound.symbol.rpartition(".")[0], bound.symbol)
            case _:
                raise Unresolved(f"unresolved receiver {ast.unparse(node)}")


def arguments(call: ast.Call, method: Function) -> None:
    args = method.args
    positional = [a.arg for a in args.posonlyargs + args.args if a.arg not in {"self", "cls"}]
    keyword_names = [k.arg for k in call.keywords if k.arg is not None]
    keywords = set(keyword_names)
    if len(keyword_names) != len(keywords):
        raise Unresolved(f"duplicate arguments for {method.name}")
    permitted = {a.arg for a in args.args + args.kwonlyargs} - {"self", "cls"}
    if args.kwarg is None and keywords - permitted:
        raise Unresolved(f"keywords {', '.join(sorted(keywords - permitted))} not in {method.name}")
    if not any(isinstance(a, ast.Starred) for a in call.args) and args.vararg is None and len(call.args) > len(positional):
        raise Unresolved(f"too many positional arguments for {method.name}")
    if not any(isinstance(a, ast.Starred) for a in call.args) and not any(k.arg is None for k in call.keywords):
        supplied = set(positional[:len(call.args)])
        if supplied & keywords:
            raise Unresolved(f"duplicate arguments for {method.name}")
        required = set(positional[:len(positional) - len(args.defaults)])
        required.update(a.arg for a, default in zip(args.kwonlyargs, args.kw_defaults) if default is None)
        if required - supplied - keywords:
            raise Unresolved(f"missing arguments {', '.join(sorted(required - supplied - keywords))} for {method.name}")


def check(mapping: Mapping, surface: Surface) -> Result:
    roots = {"client": Bound("daytona._client.Daytona"), "sandbox": Bound("daytona.v2.sandbox.AsyncSandbox")}
    problems: list[str] = []
    for entry in mapping.entries:
        errors: set[str] = set()
        locals_ = dict(roots)
        try:
            tree = form_tree(entry.form)
            receiver = mapping.receivers.get(entry.receiver, Receiver((), ()))
            candidates = [s for s in surface.classes if s.rpartition(".")[2] in receiver.names and s.startswith("daytona.v2.")]
            for producer in receiver.producers:
                for row in mapping.entries:
                    if row.id == producer:
                        stmt = form_tree(row.form).body[-1]
                        if isinstance(stmt, (ast.Expr, ast.Assign)):
                            try:
                                candidates.append(surface.expression(stmt.value, roots).symbol)
                            except Unresolved:
                                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    base = node.func.value
                    if isinstance(base, ast.Name) and base.id not in locals_ and len(set(candidates)) == 1:
                        locals_[base.id] = Bound(candidates[0])
            for stmt in tree.body:
                for node in ast.walk(stmt):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        try:
                            bound = surface.expression(node.func, locals_)
                            if bound.method is None:
                                raise Unresolved(f"not callable: {ast.unparse(node.func)}")
                            arguments(node, bound.method)
                        except Unresolved as exc:
                            errors.add(exc.message)
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            locals_[target.id] = surface.expression(stmt.value, locals_)
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Attribute):
                    surface.expression(stmt.value, locals_)
        except (SyntaxError, Unresolved) as exc:
            errors.add(exc.message if isinstance(exc, Unresolved) else "cannot parse form")
        problems.extend(f"{entry.id} py {error} | {entry.form}" for error in sorted(errors))
    return {"count": len(mapping.entries), "problems": problems}


if __name__ == "__main__":
    print(json.dumps(check(parse_mapping(json.load(sys.stdin)), Surface(Path(sys.argv[1])))))
