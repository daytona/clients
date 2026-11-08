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
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
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


# --- names an annotation refers to ---------------------------------------------


@functools.lru_cache(maxsize=None)
def module_imports(module_name: str) -> dict[str, tuple[str, str | None]]:
    """Every name a module imports, ``if TYPE_CHECKING:`` blocks included:
    local name -> (absolute module, attribute or None for a module)."""
    module = sys.modules[module_name]
    try:
        tree = ast.parse(inspect.getsource(module))
    except (OSError, TypeError):
        return {}
    package = module_name if hasattr(module, "__path__") else module_name.rpartition(".")[0]
    found: dict[str, tuple[str, str | None]] = {}
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.ImportFrom):
            base = stmt.module or ""
            if stmt.level:
                parent = package.split(".")
                parent = parent[: len(parent) - stmt.level + 1]
                base = ".".join([*parent, *([base] if base else [])])
            for alias in stmt.names:
                found.setdefault(alias.asname or alias.name, (base, alias.name))
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.asname:
                    found.setdefault(alias.asname, (alias.name, None))
                else:
                    top = alias.name.split(".")[0]
                    found.setdefault(top, (top, None))
    return found


@dataclasses.dataclass(frozen=True)
class Resolved:
    obj: object
    home: str  # the module that defines it
    name: str  # its name there


def resolve(module_name: str, dotted: list[str]) -> Resolved | None:
    """What ``a.b.c`` written in ``module_name`` refers to."""
    head, rest = dotted[0], dotted[1:]
    found = resolve_name(module_name, head)
    while found is not None and rest:
        part, rest = rest[0], rest[1:]
        if isinstance(found.obj, types.ModuleType):
            found = resolve_name(found.obj.__name__, part)
        else:
            return None
    return found


@functools.lru_cache(maxsize=None)
def resolve_name(module_name: str, name: str) -> Resolved | None:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    body = module_body(module_name)
    if name in body and name in vars(module):
        return Resolved(vars(module)[name], module_name, name)
    imported = module_imports(module_name).get(name)
    if imported is not None:
        source, attr = imported
        if attr is None:
            try:
                return Resolved(importlib.import_module(source), source, source)
            except ImportError:
                return None
        try:
            return Resolved(importlib.import_module(f"{source}.{attr}"), f"{source}.{attr}", attr)
        except ImportError:
            pass
        if source.startswith("daytona"):
            return resolve_name(source, attr)
        try:
            return Resolved(getattr(importlib.import_module(source), attr), source, attr)
        except (ImportError, AttributeError):
            return None
    if name in vars(module):
        return Resolved(vars(module)[name], module_name, name)
    return None


def referenced_names(node: ast.expr | None, *, skip_callables: bool = False) -> list[list[str]]:
    """The dotted names an annotation uses (string forward references parsed,
    ``Literal`` values skipped; with ``skip_callables``, the inside of
    ``Callable[...]`` too, whose argument types flow out, not in)."""
    found: list[list[str]] = []

    def dotted(expr: ast.expr) -> list[str] | None:
        if isinstance(expr, ast.Name):
            return [expr.id]
        if isinstance(expr, ast.Attribute):
            head = dotted(expr.value)
            return None if head is None else [*head, expr.attr]
        return None

    def walk(expr: ast.AST | None) -> None:
        if expr is None:
            return
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            try:
                walk(ast.parse(expr.value, mode="eval").body)
            except SyntaxError:
                pass
            return
        if isinstance(expr, ast.Subscript):
            head = dotted(expr.value)
            last = head[-1] if head else ""
            if last == "Literal" or (skip_callables and last == "Callable"):
                return
            walk(expr.value)
            walk(expr.slice)
            return
        name = dotted(expr) if isinstance(expr, (ast.Name, ast.Attribute)) else None
        if name is not None:
            found.append(name)
            return
        for child in ast.iter_child_nodes(expr):
            walk(child)

    walk(node)
    return found


# --- the surface ----------------------------------------------------------------


class Surface:
    """Both namespaces. Exported names are listed under their name in their
    namespace's file. A ``daytona`` class or type alias that a listed
    signature uses but no namespace exports (an options type, a ``Literal``
    alias, the v2 client behind the root's ``.v2``) is listed too, under
    ``~Name``, in the file of the first namespace that reaches it."""

    def __init__(self) -> None:
        self.modules = {name: importlib.import_module(name) for name, _, _ in NAMESPACES}
        # Declarations listed under a name of their own, by identity.
        self.named: dict[int, str] = {}
        self.used: set[str] = set()
        self.named_aliases: dict[tuple[str, str], str] = {}
        # The namespace that exports a declaration: a class another namespace
        # exports prints qualified (`daytona.DaytonaError` in daytona.v2).
        self.namespace_of: dict[int, str] = {}
        self.current_namespace = ""
        for module_name, _, _ in NAMESPACES:
            module = self.modules[module_name]
            for name in sorted(module.__all__):
                self.used.add(name)
                obj = getattr(module, name)
                if own_object(obj):
                    self.named.setdefault(id(obj), name)
                    self.namespace_of.setdefault(id(obj), module_name)
        self.lines: dict[str, list[str]] = {name: [] for name, _, _ in NAMESPACES}
        self.current: list[str] = []
        self.public_constructors = False
        self.queue: list[tuple[str, object]] = []
        # A dataclass constructor is public when users build the instance
        # to pass it in: the class types a parameter of a method users call
        # (one of an exported class, or of a class they get an instance of),
        # or a field of such a class. Decided once everything is listed.
        self.owner = MODULE_LEVEL
        self.roots: set[int] = {MODULE_LEVEL}
        self.public_init: set[int] = set()
        # Dataclasses whose fields are all private: built by a factory.
        self.factory_built: set[int] = set()
        self.param_refs: dict[int, set[int]] = {}
        self.out_refs: dict[int, set[int]] = {}
        self.field_refs: dict[int, set[int]] = {}
        self._alias_classes: dict[tuple[str, str, bool], set[int]] = {}
        # TypedDicts and protocols, where listed: the members a caller
        # supplies when passing one in (see `passed-in` lines).
        self.supplied_at: dict[int, tuple[str, list[str]]] = {}
        self.returned: set[int] = set()

    def run(self) -> dict[str, list[str]]:
        # The v2 namespace first, so a declaration both reach is listed with v2.
        for module_name, _, public_constructors in reversed(NAMESPACES):
            self.current = self.lines[module_name]
            self.current_namespace = module_name
            self.public_constructors = public_constructors
            self.namespace(self.modules[module_name])
            while self.queue:
                name, obj = self.queue.pop(0)
                self.public_constructors = False
                self.value(name, obj)
        inputs = self.built_by_callers()
        for lines in self.lines.values():
            for i, line in enumerate(lines):
                if CTOR_MARK in line:
                    head, _, rest = line.partition(CTOR_MARK)
                    ident, _, tail = rest.partition(" ")
                    public = int(ident) in inputs and int(ident) not in self.factory_built
                    kind = "constructor" if public else "internal-constructor"
                    lines[i] = f"{head}{kind} {tail}"
        # A new required member of a type callers pass in breaks them.
        for ident in sorted(inputs & set(self.supplied_at)):
            name, lines = self.supplied_at[ident]
            # `also-returned`: users read it too, so its keys stay required for them.
            lines.append(f"{name}: passed-in{' also-returned' if ident in self.returned else ''}")
        return self.lines

    def built_by_callers(self) -> set[int]:
        def closure(start: set[int], edges: dict[int, set[int]]) -> set[int]:
            found, frontier = set(start), list(start)
            while frontier:
                for ref in edges.get(frontier.pop(), ()):
                    if ref not in found:
                        found.add(ref)
                        frontier.append(ref)
            return found

        callable_by_users = closure(self.roots, self.out_refs)
        # Read by users: named in an output position of something they reach.
        self.returned = set().union(*(self.out_refs.get(o, set()) for o in callable_by_users))
        passed_in = set(self.public_init)
        for owner in callable_by_users:
            passed_in |= self.param_refs.get(owner, set())
        return closure(passed_in, self.field_refs)

    def emit(self, name: str, kind: str, detail: str = "") -> None:
        detail = " ".join(detail.split())
        self.current.append(f"{name}: {kind}{' ' + detail if detail else ''}")

    # --- reaching -----------------------------------------------------------

    def want(self, found: Resolved) -> int | None:
        """List ``found`` under ``~Name`` if it is an unlisted ``daytona``
        class or type alias; return its identity when it is a class."""
        obj = found.obj
        if isinstance(obj, type):
            if not obj.__module__.startswith("daytona") or obj.__module__.startswith("daytona.v0"):
                return None
            if hasattr(obj, "_root_form") and hasattr(obj, "_load"):
                return None
            name = obj.__name__
            home = obj.__module__
        else:
            if isinstance(obj, (types.ModuleType, typing.TypeVar, *SCALARS)) or callable(obj) and inspect.isroutine(obj):
                return None
            if not found.home.startswith("daytona") or found.home.startswith("daytona.v0"):
                return None
            if not any(isinstance(s, (ast.Assign, ast.AnnAssign)) for s in module_body(found.home).get(found.name, [])):
                return None
            name, home = found.name, found.home
            # Type aliases go by where they are defined: typing caches equal
            # unions, so two aliases can be one object.
            if any(name in m.__all__ and getattr(m, name) is obj for m in self.modules.values()):
                return None
            if (home, name) not in self.named_aliases:
                self.named_aliases[(home, name)] = self.new_key(name, home)
                self.queue.append((self.named_aliases[(home, name)], obj))
            return None
        if id(obj) not in self.named:
            self.named[id(obj)] = self.new_key(name, home)
            self.queue.append((self.named[id(obj)], obj))
        return id(obj)

    def new_key(self, name: str, home: str) -> str:
        key = f"~{name}"
        if name in self.used or key in self.used:
            key = f"~{name}@{home.replace('.', '/')}"
        self.used.add(key)
        return key

    def classes_in(self, node: ast.expr | None, module_name: str, *, skip_callables: bool = False) -> set[int]:
        """List every unlisted declaration an annotation names; return the
        classes it names, through the type aliases it uses."""
        classes: set[int] = set()
        for dotted in referenced_names(node, skip_callables=skip_callables):
            found = resolve(module_name, dotted)
            if found is None:
                continue
            self.want(found)
            if isinstance(found.obj, type):
                classes.add(id(found.obj))
            elif not isinstance(found.obj, types.ModuleType) and found.home.startswith("daytona"):
                classes |= self.alias_classes(found.home, found.name, skip_callables)
        return classes

    def alias_classes(self, home: str, name: str, skip_callables: bool) -> set[int]:
        key = (home, name, skip_callables)
        if key in self._alias_classes:
            return self._alias_classes[key]
        self._alias_classes[key] = set()  # a self-referencing alias ends here
        found: set[int] = set()
        for stmt in module_body(home).get(name, []):
            value = stmt.value if isinstance(stmt, (ast.Assign, ast.AnnAssign)) else None
            if value is not None:
                found |= self.classes_in(value, home, skip_callables=skip_callables)
        self._alias_classes[key] = found
        return found

    def reach(self, node: ast.expr | None, module_name: str, *, role: str) -> None:
        """Reach what an annotation names. ``role`` says how a caller meets
        it on the declaration being listed: ``param`` (passed in), ``out``
        (read or returned) or ``field`` (a dataclass or TypedDict field)."""
        classes = self.classes_in(node, module_name)
        owner = self.owner
        if role == "param":
            self.param_refs.setdefault(owner, set()).update(self.classes_in(node, module_name, skip_callables=True))
        elif role == "field":
            self.field_refs.setdefault(owner, set()).update(classes)
            self.out_refs.setdefault(owner, set()).update(classes)
        else:
            self.out_refs.setdefault(owner, set()).update(classes)

    def annotation(self, node: ast.expr | None, module_name: str, *, role: str = "out") -> str:
        self.reach(node, module_name, role=role)
        return annotation(node)

    def signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef, module_name: str, *, drop_first: bool, public: bool = True) -> str:
        if public:
            a = node.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                if arg is not None and arg.annotation is not None:
                    self.reach(arg.annotation, module_name, role="param")
            self.reach(node.returns, module_name, role="out")
        return signature(node, drop_first=drop_first)

    # A class from this package prints by its listed name; others by
    # ``module.qualname`` (builtins bare).
    def type_name(self, cls: type) -> str:
        if cls.__module__ == "builtins":
            return cls.__qualname__
        if id(cls) in self.named:
            namespace = self.namespace_of.get(id(cls))
            if namespace is not None and namespace != self.current_namespace:
                return f"{namespace}.{self.named[id(cls)]}"
            return self.named[id(cls)]
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
            self.want(Resolved(obj, obj.__module__, obj.__name__))
            return self.type_name(obj)
        if obj is Ellipsis:
            return "..."
        if isinstance(obj, list):
            return f"[{', '.join(self.runtime_type(a) for a in obj)}]"
        return str(obj).replace("typing.", "")

    # --- emitters ---

    def namespace(self, module: types.ModuleType) -> None:
        names: list[str] = list(module.__all__)
        canonical: dict[int, str] = {}
        for name in sorted(names):
            obj = getattr(module, name)
            if isinstance(obj, SCALARS):
                continue
            own = getattr(obj, "__name__", None) == name
            if id(obj) not in canonical or own:
                canonical[id(obj)] = name
        for name in names:
            obj = getattr(module, name)
            if not isinstance(obj, SCALARS) and canonical[id(obj)] != name:
                self.emit(name, "alias", f"= {canonical[id(obj)]}")
                continue
            if own_object(obj):
                self.named[id(obj)] = name
                if isinstance(obj, type):
                    self.roots.add(id(obj))
            self.value(name, obj, module.__name__)

    def value(self, name: str, obj: object, module_name: str | None = None) -> None:
        if isinstance(obj, type):
            self.cls(name, obj)
        elif isinstance(obj, types.ModuleType):
            self.module_(name, obj)
        elif inspect.isfunction(obj):
            body = module_body(obj.__module__)
            for node in function_defs(obj, body):
                self.emit(name, def_kind(node), self.signature(node, obj.__module__, drop_first=False))
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

    def listed(self, cls: type) -> bool:
        return id(cls) in self.named or not cls.__module__.startswith("daytona")

    def public_bases(self, cls: type) -> list[str]:
        names: list[str] = []
        for base in cls.__bases__:
            if base is object or base in (typing.Generic, typing.Protocol):
                continue
            if self.listed(base):
                names.append(self.type_name(base))
            else:
                names.extend(self.public_bases(base))
        return list(dict.fromkeys(names))

    def own_classes(self, cls: type) -> list[type]:
        """``cls`` and the private package classes it inherits members from."""
        return [k for k in cls.__mro__ if k.__module__.startswith("daytona") and (k is cls or not self.listed(k))]

    def cls(self, name: str, cls: type) -> None:
        outer, self.owner = self.owner, id(cls)
        try:
            self.cls_body(name, cls)
        finally:
            self.owner = outer

    def cls_body(self, name: str, cls: type) -> None:
        if hasattr(cls, "_root_form") and hasattr(cls, "_load"):
            self.emit(name, "annotation-only class", f"= {cls._root_form}")
            return
        bases = self.public_bases(cls)
        based = f"({', '.join(bases)})" if bases else ""
        # Generic parameters, like TypeScript's `interface <T>`.
        params = [p for p in getattr(cls, "__parameters__", ()) if isinstance(p, typing.TypeVar)]
        if params:
            based = f"<{', '.join(p.__name__ for p in params)}>{' ' + based if based else ''}"
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
            total = "total" if cls.__total__ else "total=False"
            self.emit(name, "typeddict", f"{based.split(' ', 1)[0]} {total}" if based.startswith("<") else total)
            self.typeddict(name, cls)
            return
        elif getattr(cls, "_is_protocol", False):
            self.emit(name, "protocol", based)
            self.supplied_at[id(cls)] = (name, self.current)
        else:
            self.emit(name, "class", based)
            self.constructor(name, cls)
        self.members(name, cls)

    def constructor_kind(self, cls: type) -> str:
        return "constructor" if self.public_constructors or issubclass(cls, BaseException) else "internal-constructor"

    def constructor(self, name: str, cls: type) -> None:
        for klass in cls.__mro__:
            if "__init__" not in vars(klass):
                continue
            if klass is not cls and self.listed(klass):
                return  # inherited from a class listed on its own (or from a builtin)
            kind = self.constructor_kind(cls)
            for node in function_defs(vars(klass)["__init__"], class_body(klass)):
                text = self.signature(node, klass.__module__, drop_first=True, public=kind == "constructor")
                self.emit(f"{name}.__init__", kind, text)
            return

    def fields(self, name: str, cls: type) -> None:
        positional: list[str] = []
        keyword: list[str] = []
        for field in dataclasses.fields(cls):
            node = None
            owner: type = cls
            for klass in cls.__mro__:
                stmts = class_body(klass).get(field.name, []) if dataclasses.is_dataclass(klass) else []
                node = next((s for s in stmts if isinstance(s, ast.AnnAssign)), None)
                if node is not None:
                    owner = klass
                    break
            ann = ""
            if node is not None:
                ann = self.annotation(node.annotation, owner.__module__, role="field")
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
        if params and all(p.startswith("_") for p in params if p != "*"):
            self.factory_built.add(id(cls))
        if self.constructor_kind(cls) == "constructor":
            self.public_init.add(id(cls))
        self.emit(f"{name}.__init__", f"{CTOR_MARK}{id(cls)}", f"({', '.join(params)}) -> None")

    def typeddict(self, name: str, cls: type) -> None:
        # Required-ness from the source: under `from __future__ import
        # annotations`, `__required_keys__` cannot see `Required[...]`.
        self.supplied_at[id(cls)] = (name, self.current)
        for key in cls.__annotations__:
            node = None
            owner: type = cls
            for klass in cls.__mro__:
                stmts = class_body(klass).get(key, [])
                node = next((s for s in stmts if isinstance(s, ast.AnnAssign)), None)
                if node is not None:
                    owner = klass
                    break
            ann = ""
            if node is not None:
                ann = self.annotation(node.annotation, owner.__module__, role="field")
            required = key_required(ann, total=bool(getattr(owner, "__total__", True)))
            self.emit(f"{name}.{key}", "key" if required else "optional key", ann)

    def members(self, name: str, cls: type) -> None:
        seen: set[str] = set()
        dataclass_fields = {f.name for f in dataclasses.fields(cls)} if dataclasses.is_dataclass(cls) else set()
        is_enum = issubclass(cls, enum.Enum)
        for klass in self.own_classes(cls):
            body = class_body(klass)
            module_name = klass.__module__
            for attr, raw in vars(klass).items():
                if attr in seen or (attr.startswith("_") and attr not in PROTOCOL_DUNDERS):
                    continue
                seen.add(attr)
                if attr in dataclass_fields or (is_enum and isinstance(raw, cls)):
                    continue
                self.member(f"{name}.{attr}", raw, body, module_name)
            # Annotated attributes without a class-level value.
            for attr, stmts in body.items():
                if attr in seen or attr.startswith("_") or attr in dataclass_fields:
                    continue
                ann = next((s for s in stmts if isinstance(s, ast.AnnAssign)), None)
                if ann is not None:
                    seen.add(attr)
                    text = self.annotation(ann.annotation, module_name)
                    kind = "classvar" if text.startswith("ClassVar") else "attribute"
                    self.emit(f"{name}.{attr}", kind, text)
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
                            ann = self.annotation(stmt.annotation, module_name) if isinstance(stmt, ast.AnnAssign) else ""
                            self.emit(f"{name}.{t.attr}", "attribute", ann)

    def member(self, name: str, raw: object, body: dict[str, list[ast.AST]], module_name: str) -> None:
        if isinstance(raw, (staticmethod, classmethod)):
            prefix = "staticmethod" if isinstance(raw, staticmethod) else "classmethod"
            for node in function_defs(raw.__func__, body):
                kind = f"{prefix} {def_kind(node)}"
                self.emit(name, kind, self.signature(node, module_name, drop_first=isinstance(raw, classmethod)))
        elif isinstance(raw, (property, functools.cached_property)):
            getter = raw.fget if isinstance(raw, property) else raw.func
            node = function_defs(getter, body)[-1]
            settable = isinstance(raw, property) and raw.fset is not None
            kind = "property" if settable else "readonly property"
            if settable and raw.fset is not None:
                for setter in function_defs(raw.fset, body)[-1:]:
                    self.signature(setter, module_name, drop_first=True)
            self.emit(name, kind, self.annotation(node.returns, module_name))  # type: ignore[union-attr]
        elif inspect.isfunction(raw):
            for node in function_defs(raw, body):
                self.emit(name, def_kind(node), self.signature(node, module_name, drop_first=True))
        elif isinstance(raw, type):
            self.emit(name, "class", self.type_name(raw))
        elif isinstance(raw, SCALARS):
            stmts = body.get(name.rsplit(".", 1)[1], [])
            node = next((s.annotation for s in stmts if isinstance(s, ast.AnnAssign)), None)
            ann = self.annotation(node, module_name) if node is not None else ""
            kind = "classvar" if ann.startswith("ClassVar") else "attribute"
            self.emit(name, kind, f"{ann + ' ' if ann else ''}= {raw!r}")
        else:
            stmts = body.get(name.rsplit(".", 1)[1], [])
            node = next((s.annotation for s in stmts if isinstance(s, ast.AnnAssign)), None)
            ann = self.annotation(node, module_name) if node is not None else ""
            self.emit(name, "attribute", ann or type(raw).__name__)


CTOR_MARK = "\x00ctor:"
MODULE_LEVEL = 0  # the owner of module-level functions and aliases


def key_required(annotation_text: str, *, total: bool) -> bool:
    """Whether a TypedDict key is required, from its annotation as written."""
    inner = annotation_text.removeprefix("ReadOnly[")
    if inner.startswith("Required["):
        return True
    if inner.startswith("NotRequired["):
        return False
    return total


def own_object(obj: object) -> bool:
    """Whether an exported value is this package's own declaration (an
    export like ``MacBootMode = str`` names a builtin, which keeps its name)."""
    if isinstance(obj, SCALARS):
        return False
    return not (isinstance(obj, type) and not obj.__module__.startswith("daytona"))


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
    surfaces = Surface().run()
    for module_name, out, _ in NAMESPACES:
        text = render(module_name, surfaces[module_name])
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
