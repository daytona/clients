# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Write the public surface of ``daytona`` and ``daytona.v2`` to ``api-surface/``.

One declaration per line, sorted: every name in the namespace's ``__all__``
with its kind and signature; for classes the constructor, methods (``def`` or
``async def``), properties, attributes, dataclass fields, enum members and
TypedDict keys. Annotations and defaults are printed from the source (AST),
never from runtime ``repr()``, so the text is identical on every supported
Python version.

    python gen/api_surface.py            # regenerate the committed files
    python gen/api_surface.py --check    # exit 1 if a committed file is stale

Line format, shared with the TypeScript and CLI snapshots and read by
``scripts/breaking/breaking_changes.py``: ``<name>: <kind>[ <signature>]``.
"""

from __future__ import annotations

import ast
import collections.abc
import dataclasses
import enum
import functools
import importlib
import inspect
import sys
import textwrap
import types
import typing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

REGENERATE = "python gen/api_surface.py (in sdk-python/)"
NAMESPACES = (
    # (module, output file, whether class constructors are public API there)
    ("daytona", "api-surface/daytona.api.txt", True),
    ("daytona.v2", "api-surface/daytona.v2.api.txt", False),
)
# Dunder methods that are part of how a class is used (protocols).
PROTOCOL_DUNDERS = frozenset(
    {
        "__call__",
        "__enter__",
        "__exit__",
        "__aenter__",
        "__aexit__",
        "__iter__",
        "__next__",
        "__aiter__",
        "__anext__",
        "__len__",
        "__getitem__",
        "__contains__",
        "__bool__",
        "__await__",
    }
)
# A long union or collection prints one member per line.
LONG = 120
SCALARS = (str, int, float, bool, type(None))
TYPING_RENAMES = {
    "List": "list",
    "Dict": "dict",
    "Set": "set",
    "FrozenSet": "frozenset",
    "Tuple": "tuple",
    "Type": "type",
}


# --- annotation text --------------------------------------------------------


class _Normalize(ast.NodeTransformer):
    """Spell an annotation one way: ``X | None`` for ``Optional[X]``, builtin
    generics, no ``typing.`` prefix, string forward references unquoted."""

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return node
            return self.visit(parsed)
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id in ("typing", "typing_extensions", "t"):
            return ast.Name(id=TYPING_RENAMES.get(node.attr, node.attr), ctx=ast.Load())
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.Name(id=TYPING_RENAMES.get(node.id, node.id), ctx=ast.Load())

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        if isinstance(node.value, ast.Name) and node.value.id == "Literal":
            return node  # the strings inside are values, not forward references
        self.generic_visit(node)
        name = node.value.id if isinstance(node.value, ast.Name) else None
        if name == "Optional":
            return ast.BinOp(left=node.slice, op=ast.BitOr(), right=ast.Constant(value=None))
        if name == "Union":
            members = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
            return functools.reduce(lambda a, b: ast.BinOp(left=a, op=ast.BitOr(), right=b), members)
        return node


def annotation(node: ast.expr | None) -> str:
    if node is None:
        return ""
    return ast.unparse(_Normalize().visit(node))


def expression(node: ast.expr) -> str:
    return ast.unparse(node)


# --- sources ----------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def class_body(cls: type) -> dict[str, list[ast.AST]]:
    """Top-level statements of a class body, by the name they define."""
    try:
        source = textwrap.dedent(inspect.getsource(cls))
    except (OSError, TypeError):
        return {}
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    found: dict[str, list[ast.AST]] = {}
    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(stmt.name, []).append(stmt)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            found.setdefault(stmt.target.id, []).append(stmt)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(stmt)
    return found


@functools.lru_cache(maxsize=None)
def module_body(module_name: str) -> dict[str, list[ast.AST]]:
    module = sys.modules[module_name]
    try:
        tree = ast.parse(inspect.getsource(module))
    except (OSError, TypeError):
        return {}
    found: dict[str, list[ast.AST]] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.setdefault(stmt.name, []).append(stmt)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            found.setdefault(stmt.target.id, []).append(stmt)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(stmt)
    return found


def is_overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(annotation(d).endswith("overload") for d in node.decorator_list)


def function_defs(func: typing.Callable[..., object], body: dict[str, list[ast.AST]]) -> list[ast.AST]:
    """The ``def`` nodes of a function: its overloads, else its implementation."""
    func = inspect.unwrap(func)
    name = getattr(func, "__name__", "")
    defs = [n for n in body.get(name, []) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not defs:
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        except (OSError, TypeError, SyntaxError) as exc:
            raise SystemExit(f"api_surface: no source for {func!r}: {exc}") from exc
        defs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    overloads = [n for n in defs if is_overload(n)]
    return overloads or defs[-1:]


def signature(node: ast.FunctionDef | ast.AsyncFunctionDef, *, drop_first: bool) -> str:
    a = node.args
    positional = [*a.posonlyargs, *a.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    parts: list[str] = []

    def param(arg: ast.arg, default: ast.expr | None, prefix: str = "") -> str:
        text = prefix + arg.arg
        if arg.annotation is not None:
            text += f": {annotation(arg.annotation)}"
        if default is not None:
            text += f" = {expression(default)}"
        return text

    for i, (arg, default) in enumerate(zip(positional, defaults)):
        if i == 0 and drop_first:
            continue
        parts.append(param(arg, default))
        if a.posonlyargs and arg is a.posonlyargs[-1]:
            parts.append("/")
    if a.vararg is not None:
        parts.append(param(a.vararg, None, "*"))
    elif a.kwonlyargs:
        parts.append("*")
    parts.extend(param(arg, default) for arg, default in zip(a.kwonlyargs, a.kw_defaults))
    if a.kwarg is not None:
        parts.append(param(a.kwarg, None, "**"))
    returns = f" -> {annotation(node.returns)}" if node.returns is not None else ""
    return f"({', '.join(parts)}){returns}"


def def_kind(node: ast.AST) -> str:
    return "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"


# --- runtime values -----------------------------------------------------------


class Namespace:
    def __init__(self, module_name: str, public_constructors: bool) -> None:
        self.module = importlib.import_module(module_name)
        self.public_constructors = public_constructors
        self.names: list[str] = list(self.module.__all__)
        # Every class any namespace exports, by identity: members a class
        # inherits from one of them are listed under that class, not again.
        self.exported: dict[int, str] = {}
        for name in sorted(self.names):
            obj = getattr(self.module, name)
            if not isinstance(obj, SCALARS):
                self.exported.setdefault(id(obj), name)
        self.lines: list[str] = []

    def emit(self, name: str, kind: str, detail: str = "") -> None:
        detail = " ".join(detail.split())
        self.lines.append(f"{name}: {kind}{' ' + detail if detail else ''}")

    # A class from this package prints by its exported name; others by
    # ``module.qualname`` (builtins bare).
    def type_name(self, cls: type) -> str:
        if id(cls) in self.exported:
            return self.exported[id(cls)]
        if cls.__module__ == "builtins":
            return cls.__qualname__
        if cls.__module__ in ("typing", "collections.abc", "typing_extensions"):
            return cls.__qualname__
        return f"{cls.__module__}.{cls.__qualname__}"

    def runtime_type(self, obj: object) -> str:
        """Spell a runtime typing object (module-level aliases)."""
        if obj is None or obj is type(None):
            return "None"
        if isinstance(obj, typing.TypeVar):
            return obj.__name__
        if isinstance(obj, typing.ForwardRef):
            return obj.__forward_arg__
        if isinstance(obj, str):
            return obj
        origin = typing.get_origin(obj)
        args = typing.get_args(obj)
        if origin is typing.Literal:
            return f"Literal[{', '.join(repr(a) for a in args)}]"
        if origin is typing.Union or (hasattr(types, "UnionType") and origin is types.UnionType):
            return " | ".join(self.runtime_type(a) for a in args)
        if origin is collections.abc.Callable:
            params, ret = args
            inner = "..." if params is Ellipsis else f"[{', '.join(self.runtime_type(p) for p in params)}]"
            return f"Callable[{inner}, {self.runtime_type(ret)}]"
        if origin is not None:
            head = self.type_name(origin) if isinstance(origin, type) else str(origin).replace("typing.", "")
            return f"{head}[{', '.join(self.runtime_type(a) for a in args)}]" if args else head
        if isinstance(obj, type):
            return self.type_name(obj)
        if obj is Ellipsis:
            return "..."
        if isinstance(obj, list):
            return f"[{', '.join(self.runtime_type(a) for a in obj)}]"
        return str(obj).replace("typing.", "")

    # --- emitters ---

    def run(self) -> list[str]:
        canonical: dict[int, str] = {}
        for name in sorted(self.names):
            obj = getattr(self.module, name)
            if isinstance(obj, SCALARS):
                continue
            own = getattr(obj, "__name__", None) == name
            if id(obj) not in canonical or own:
                canonical[id(obj)] = name
        for name in self.names:
            obj = getattr(self.module, name)
            if not isinstance(obj, SCALARS) and canonical[id(obj)] != name:
                self.emit(name, "alias", f"= {canonical[id(obj)]}")
                continue
            self.exported[id(obj)] = name
            self.value(name, obj)
        return self.lines

    def value(self, name: str, obj: object) -> None:
        if isinstance(obj, type):
            self.cls(name, obj)
        elif isinstance(obj, types.ModuleType):
            self.module_(name, obj)
        elif inspect.isfunction(obj):
            body = module_body(obj.__module__)
            for node in function_defs(obj, body):
                self.emit(name, def_kind(node), signature(node, drop_first=False))
        elif isinstance(obj, SCALARS):
            self.emit(name, "const", f"{type(obj).__name__} = {obj!r}")
        elif isinstance(obj, (frozenset, set, tuple, list)):
            self.collection(name, obj)
        elif isinstance(obj, (dict, types.MappingProxyType)):
            self.mapping(name, obj)
        else:
            self.alias_type(name, obj)

    def collection(self, name: str, obj: frozenset[object] | set[object] | tuple[object, ...] | list[object]) -> None:
        if not all(isinstance(item, SCALARS) for item in obj):
            self.emit(name, "const", type(obj).__name__)
            return
        items = sorted(obj, key=repr) if isinstance(obj, (frozenset, set)) else list(obj)
        text = f"{type(obj).__name__}({', '.join(repr(i) for i in items)})"
        if len(text) <= LONG:
            self.emit(name, "const", text)
            return
        self.emit(name, "const", type(obj).__name__)
        for item in items:
            self.emit(name, "element", repr(item))

    def mapping(self, name: str, obj: typing.Mapping[object, object]) -> None:
        kind = "mappingproxy" if isinstance(obj, types.MappingProxyType) else "dict"
        self.emit(name, "const", kind)
        for key in sorted(obj, key=repr):
            item = obj[key]
            if isinstance(item, SCALARS):
                shown = repr(item)
            elif isinstance(item, (frozenset, set, tuple, list)) and all(isinstance(i, SCALARS) for i in item):
                values = sorted(item, key=repr) if isinstance(item, (frozenset, set)) else list(item)
                shown = f"{type(item).__name__}({', '.join(repr(i) for i in values)})"
            else:
                shown = type(item).__name__
            self.emit(name, "item", f"{key!r}: {shown}")

    def alias_type(self, name: str, obj: object) -> None:
        origin = typing.get_origin(obj)
        text = self.runtime_type(obj)
        if len(text) > LONG and origin in (typing.Literal, typing.Union) or (
            len(text) > LONG and hasattr(types, "UnionType") and origin is types.UnionType
        ):
            self.emit(name, "type", "= Literal" if origin is typing.Literal else "= union")
            for arg in typing.get_args(obj):
                member = repr(arg) if origin is typing.Literal else self.runtime_type(arg)
                self.emit(name, "union-member", member)
            return
        self.emit(name, "type", f"= {text}")

    def module_(self, name: str, module: types.ModuleType) -> None:
        self.emit(name, "module")
        public = getattr(module, "__all__", None)
        if public is None:
            body = module_body(module.__name__)
            public = [n for n in body if not n.startswith("_")]
        for member in sorted(public):
            self.value(f"{name}.{member}", getattr(module, member))

    def public_bases(self, cls: type) -> list[str]:
        names: list[str] = []
        for base in cls.__bases__:
            if base is object or base in (typing.Generic, typing.Protocol):
                continue
            if id(base) in self.exported or not base.__module__.startswith("daytona"):
                names.append(self.type_name(base))
            else:
                names.extend(self.public_bases(base))
        return list(dict.fromkeys(names))

    def own_classes(self, cls: type) -> list[type]:
        """``cls`` and the private package classes it inherits members from."""
        owners = []
        for klass in cls.__mro__:
            if klass is not cls and (id(klass) in self.exported or not klass.__module__.startswith("daytona")):
                continue
            if klass.__module__.startswith("daytona"):
                owners.append(klass)
        return owners

    def cls(self, name: str, cls: type) -> None:
        bases = self.public_bases(cls)
        based = f"({', '.join(bases)})" if bases else ""
        if hasattr(cls, "_root_form") and hasattr(cls, "_load"):
            self.emit(name, "annotation-only class", f"= {cls._root_form}")
            return
        if dataclasses.is_dataclass(cls):
            params = cls.__dataclass_params__  # type: ignore[attr-defined]
            flags = " frozen" if params.frozen else ""
            self.emit(name, f"dataclass{flags}", based)
            self.fields(name, cls)
        elif issubclass(cls, enum.Enum):
            self.emit(name, "enum", based)
            for member in cls:
                self.emit(f"{name}.{member.name}", "enum-member", f"= {member.value!r}")
        elif typing.is_typeddict(cls) if hasattr(typing, "is_typeddict") else False:
            self.emit(name, "typeddict", "total" if cls.__total__ else "total=False")
            self.typeddict(name, cls)
            return
        elif getattr(cls, "_is_protocol", False):
            self.emit(name, "protocol", based)
        else:
            self.emit(name, "class", based)
            self.constructor(name, cls)
        self.members(name, cls)

    def constructor(self, name: str, cls: type) -> None:
        for klass in cls.__mro__:
            if "__init__" not in vars(klass):
                continue
            if klass is not cls and (id(klass) in self.exported or not klass.__module__.startswith("daytona")):
                return  # inherited from a class listed on its own (or from a builtin)
            public = self.public_constructors or issubclass(cls, BaseException)
            kind = "constructor" if public else "internal-constructor"
            for node in function_defs(vars(klass)["__init__"], class_body(klass)):
                self.emit(f"{name}.__init__", kind, signature(node, drop_first=True))
            return

    def fields(self, name: str, cls: type) -> None:
        positional: list[str] = []
        keyword: list[str] = []
        for field in dataclasses.fields(cls):
            node = None
            for klass in cls.__mro__:
                stmts = class_body(klass).get(field.name, []) if dataclasses.is_dataclass(klass) else []
                node = next((s for s in stmts if isinstance(s, ast.AnnAssign)), None)
                if node is not None:
                    break
            ann = annotation(node.annotation) if node is not None else ""
            default = ""
            if node is not None and node.value is not None:
                value = node.value
                if isinstance(value, ast.Call) and expression(value.func).endswith("field"):
                    for kw in value.keywords:
                        if kw.arg == "default":
                            default = f" = {expression(kw.value)}"
                        elif kw.arg == "default_factory":
                            default = f" = {expression(kw.value)}()"
                else:
                    default = f" = {expression(value)}"
            kw_only = getattr(field, "kw_only", False) is True
            mods = []
            if kw_only:
                mods.append("kw_only")
            if not field.init:
                mods.append("init=False")
            kind = " ".join(["field", *mods])
            self.emit(f"{name}.{field.name}", kind, f"{ann}{default}")
            if field.init:
                param = f"{field.name}{': ' + ann if ann else ''}{default}"
                (keyword if kw_only else positional).append(param)
        if not cls.__dataclass_params__.init:  # type: ignore[attr-defined]
            self.constructor(name, cls)
            return
        # The generated __init__: field order decides positional calls.
        params = positional + (["*", *keyword] if keyword else [])
        public = self.public_constructors or issubclass(cls, BaseException)
        kind = "constructor" if public else "internal-constructor"
        self.emit(f"{name}.__init__", kind, f"({', '.join(params)}) -> None")

    def typeddict(self, name: str, cls: type) -> None:
        required = getattr(cls, "__required_keys__", frozenset())
        for key in cls.__annotations__:
            node = None
            for klass in cls.__mro__:
                stmts = class_body(klass).get(key, [])
                node = next((s for s in stmts if isinstance(s, ast.AnnAssign)), None)
                if node is not None:
                    break
            ann = annotation(node.annotation) if node is not None else ""
            self.emit(f"{name}.{key}", "key" if key in required else "optional key", ann)

    def members(self, name: str, cls: type) -> None:
        seen: set[str] = set()
        dataclass_fields = {f.name for f in dataclasses.fields(cls)} if dataclasses.is_dataclass(cls) else set()
        is_enum = issubclass(cls, enum.Enum)
        for klass in self.own_classes(cls):
            body = class_body(klass)
            for attr, raw in vars(klass).items():
                if attr in seen or (attr.startswith("_") and attr not in PROTOCOL_DUNDERS):
                    continue
                seen.add(attr)
                if attr in dataclass_fields or (is_enum and isinstance(raw, cls)):
                    continue
                self.member(f"{name}.{attr}", raw, body)
            # Annotated attributes without a class-level value.
            for attr, stmts in body.items():
                if attr in seen or attr.startswith("_") or attr in dataclass_fields:
                    continue
                ann = next((s for s in stmts if isinstance(s, ast.AnnAssign)), None)
                if ann is not None:
                    seen.add(attr)
                    kind = "classvar" if annotation(ann.annotation).startswith("ClassVar") else "attribute"
                    self.emit(f"{name}.{attr}", kind, annotation(ann.annotation))
            # Public instance attributes the constructor sets.
            init = body.get("__init__", [])
            for node in init:
                if not isinstance(node, ast.FunctionDef):
                    continue
                for stmt in ast.walk(node):
                    target = stmt.target if isinstance(stmt, ast.AnnAssign) else None
                    targets = [target] if target is not None else (stmt.targets if isinstance(stmt, ast.Assign) else [])
                    for t in targets:
                        if (
                            isinstance(t, ast.Attribute)
                            and isinstance(t.value, ast.Name)
                            and t.value.id == "self"
                            and not t.attr.startswith("_")
                            and t.attr not in seen
                        ):
                            seen.add(t.attr)
                            ann = annotation(stmt.annotation) if isinstance(stmt, ast.AnnAssign) else ""
                            self.emit(f"{name}.{t.attr}", "attribute", ann)

    def member(self, name: str, raw: object, body: dict[str, list[ast.AST]]) -> None:
        if isinstance(raw, (staticmethod, classmethod)):
            prefix = "staticmethod" if isinstance(raw, staticmethod) else "classmethod"
            for node in function_defs(raw.__func__, body):
                kind = f"{prefix} {def_kind(node)}"
                self.emit(name, kind, signature(node, drop_first=isinstance(raw, classmethod)))
        elif isinstance(raw, (property, functools.cached_property)):
            getter = raw.fget if isinstance(raw, property) else raw.func
            node = function_defs(getter, body)[-1]
            settable = isinstance(raw, property) and raw.fset is not None
            kind = "property" if settable else "readonly property"
            self.emit(name, kind, annotation(node.returns))  # type: ignore[union-attr]
        elif inspect.isfunction(raw):
            for node in function_defs(raw, body):
                self.emit(name, def_kind(node), signature(node, drop_first=True))
        elif isinstance(raw, type):
            self.emit(name, "class", self.type_name(raw))
        elif isinstance(raw, SCALARS):
            stmts = body.get(name.rsplit(".", 1)[1], [])
            ann = next((annotation(s.annotation) for s in stmts if isinstance(s, ast.AnnAssign)), "")
            kind = "classvar" if ann.startswith("ClassVar") else "attribute"
            self.emit(name, kind, f"{ann + ' ' if ann else ''}= {raw!r}")
        else:
            stmts = body.get(name.rsplit(".", 1)[1], [])
            ann = next((annotation(s.annotation) for s in stmts if isinstance(s, ast.AnnAssign)), "")
            self.emit(name, "attribute", ann or type(raw).__name__)


def render(module_name: str, lines: list[str]) -> str:
    def key(line: str) -> tuple[str, str]:
        name, _, rest = line.partition(": ")
        return name, rest

    header = [
        f"# Public surface of {module_name} (__all__).",
        f"# Generated by gen/api_surface.py; do not edit. Regenerate: {REGENERATE}.",
    ]
    return "\n".join([*header, *sorted(lines, key=key)]) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = 0
    for module_name, out, public_constructors in NAMESPACES:
        text = render(module_name, Namespace(module_name, public_constructors).run())
        target = ROOT / out
        current = target.read_text() if target.exists() else ""
        if current == text:
            continue
        if check:
            stale += 1
            print(f"sdk-python/{out} is stale: the public surface of {module_name} changed.", file=sys.stderr)
            before, after = set(current.splitlines()), set(text.splitlines())
            for line in current.splitlines():
                if line not in after:
                    print(f"  - {line}", file=sys.stderr)
            for line in text.splitlines():
                if line not in before:
                    print(f"  + {line}", file=sys.stderr)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        print(f"wrote {out}")
    if stale:
        print(f"Regenerate with `{REGENERATE}` and commit the result.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
