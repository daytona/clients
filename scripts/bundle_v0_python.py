# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Bundle the v0 Python SDK into the 2.x package as `daytona.v0`.

    python3 bundle_v0_python.py <v0 sdk-python dir> <version> <tag>
    python3 bundle_v0_python.py --clean

Driven by scripts/bundle-v0.sh at publish time. The result is never committed:

1. copy `<sdk-python>/src/daytona/` to `sdk-python/src/daytona/v0/`;
2. rewrite every absolute `daytona` import (two in code at v0.223.1, plus the
   examples in docstrings) to `daytona.v0`, and fail on any left over;
3. make v0's SDK-version header report the bundled release instead of the 2.x
   distribution's version (`importlib.metadata.version("daytona")`);
4. hide v0's client classes (`Daytona`, `AsyncDaytona`, `DaytonaConfig`)
   behind private names only the root's `.v0` uses, so SDK 2.x builds a v0
   client only through `Daytona().v0`; the public names raise an ImportError
   that names the root form;
5. append `…V0` aliases for the names v2 exports too (failing when they no
   longer match v2's committed `SHARED_WITH_V0`), and `_bundle.py` with the
   provenance;
6. make v0's error classes inherit the shared root markers
   (`daytona/_errors.py`), failing when one is missing;
7. fill the `v0-sdk` extra in `pyproject.toml` with v0's runtime dependencies,
   its six generated API clients pinned to the bundled release.

`--clean` removes the bundle and empties the `v0-sdk` extra again.
"""

from __future__ import annotations

import ast
import re
import shutil
import sys
from pathlib import Path

PYTHON = Path(__file__).resolve().parents[1] / "sdk-python"
DEST = PYTHON / "src" / "daytona" / "v0"
PYPROJECT = PYTHON / "pyproject.toml"
V2_EXPORTS = PYTHON / "src" / "daytona" / "v2" / "_exports.py"

# The generated clients v0's own publish adds at the SDK's version
# (daytona/clients sdk-python/scripts/add-api-clients.sh).
API_CLIENTS = (
    "daytona_api_client",
    "daytona_api_client_async",
    "daytona_toolbox_api_client",
    "daytona_toolbox_api_client_async",
    "daytona_analytics_api_client",
    "daytona_analytics_api_client_async",
)

# Root marker (daytona/_errors.py) -> the v0 class that must inherit it, so
# `except daytona.DaytonaNotFoundError` also catches v0's errors. The keys are
# daytona._errors.SHARED_ERRORS; a v0 release missing one fails the bundle.
V0_SHARED_ERRORS = {
    "DaytonaError": "DaytonaError",
    "DaytonaBadRequestError": "DaytonaBadRequestError",
    "DaytonaAuthenticationError": "DaytonaAuthenticationError",
    "DaytonaForbiddenError": "DaytonaForbiddenError",
    "DaytonaNotFoundError": "DaytonaNotFoundError",
    "DaytonaTimeoutError": "DaytonaTimeoutError",
    "DaytonaConflictError": "DaytonaConflictError",
    "DaytonaGoneError": "DaytonaGoneError",
    "DaytonaUnprocessableEntityError": "DaytonaUnprocessableEntityError",
    "DaytonaRateLimitError": "DaytonaRateLimitError",
    "DaytonaInternalServerError": "DaytonaInternalServerError",
    "DaytonaServiceUnavailableError": "DaytonaServiceUnavailableError",
    "DaytonaConnectionError": "DaytonaConnectionError",
}
V0_ERRORS_MODULE = Path("common") / "errors.py"

_ABSOLUTE_IMPORT = re.compile(r"^(\s*)(from|import)\s+daytona(?=[.\s])(?!\.v0\b)", re.MULTILINE)
# `v0-sdk = []`, or the one-requirement-per-line list this script writes (a
# requirement can itself contain brackets: `python-socketio[client]`).
_EXTRA = re.compile(r'^v0-sdk = \[(?:\]|\n(?:[ \t]+".*",\n)*\])$', re.MULTILINE)
_METADATA_IMPORT = "from importlib.metadata import version\n"
# Any remaining lookup of the installed distribution's version: an import of
# importlib.metadata, or a version("daytona") call (not prose in docstrings).
_METADATA_LOOKUP = re.compile(
    r"^\s*(?:from\s+importlib(?:\.metadata)?\s+import\b|import\s+importlib\.metadata\b)"
    r"|\bversion\(\s*[\"']daytona[\"']\s*\)",
    re.MULTILINE,
)


# `import daytona.x` without `as` binds the name `daytona`, which in the bundle
# would be the 2.x root, not v0: rewriting it to `import daytona.v0.x` would
# leave `daytona.x.…` resolving against the host package. Refuse it instead.
_UNALIASED_DOTTED_IMPORT = re.compile(r"^\s*import\s+daytona\.[\w.]+\s*(?:,|#|$)", re.MULTILINE)


def _rewrite_imports(root: Path) -> int:
    unaliased = [
        f"{path.relative_to(root)}:{text.count(chr(10), 0, m.start()) + 1}"
        for path in root.rglob("*.py")
        for text in [path.read_text()]
        for m in _UNALIASED_DOTTED_IMPORT.finditer(text)
    ]
    if unaliased:
        raise SystemExit(
            f"bundle-v0: unaliased `import daytona.<module>` would bind the 2.x root, not v0: {unaliased}"
        )
    count = 0
    for path in root.rglob("*.py"):
        text = path.read_text()
        new, n = _ABSOLUTE_IMPORT.subn(lambda m: f"{m.group(1)}{m.group(2)} daytona.v0", text)
        if n:
            path.write_text(new)
            count += n
    leftovers = [
        f"{path.relative_to(root)}:{text.count(chr(10), 0, m.start()) + 1}"
        for path in root.rglob("*.py")
        for text in [path.read_text()]
        for m in _ABSOLUTE_IMPORT.finditer(text)
    ]
    if leftovers:
        raise SystemExit(f"bundle-v0: absolute daytona imports left in the bundle: {leftovers}")
    return count


def _pin_sdk_version(root: Path, version: str) -> list[str]:
    """v0 sends `importlib.metadata.version("daytona")` as its SDK version;
    inside 2.x that is the 2.x distribution. Point it at the bundled release."""
    patched = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        if _METADATA_IMPORT in text:
            depth = len(path.relative_to(root).parts) - 1
            dots = "." * (depth + 1)
            path.write_text(
                text.replace(
                    _METADATA_IMPORT,
                    f"from {dots}_bundle import bundled_version as version\n",
                )
            )
            patched.append(str(path.relative_to(root)))
    leftovers = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        for text in [path.read_text()]
        if _METADATA_LOOKUP.search(text)
    ]
    if leftovers:
        # A lookup the rewrite missed would report the 2.x version as v0's.
        raise SystemExit(f"bundle-v0: v0 SDK-version lookups left unrewritten in: {leftovers}")
    if not patched:
        # Without the rewrite v0 would report the 2.x version (or "unknown") as
        # X-Daytona-SDK-Version; fail like the TypeScript bundler does.
        raise SystemExit(
            "bundle-v0: v0's SDK-version lookup was not found (expected "
            f"{_METADATA_IMPORT.strip()!r}); update _METADATA_IMPORT for this v0 release"
        )
    return patched


# v0's dotenv warning tells the user to pass `api_url` to `DaytonaConfig` or set
# DAYTONA_API_URL. Under the 2.x root neither works (the root rejects api_url,
# and nothing in SDK 2.x reads DAYTONA_API_URL, Daytona().v0 included), and
# the root is the only way to build a v0 client, so the remedy is rewritten for it.
_DOTENV_REMEDY = (
    '+ f" To change the endpoint, pass `api_url` to `DaytonaConfig` or set `{name}`"\n'
    '                + " in the environment of the process.",'
)
_DOTENV_REMEDY_V0 = (
    '+ " The root `Daytona().v0` ignores `DAYTONA_API_URL` (use `DAYTONA_V0_API_URL` for"\n'
    '                + " non-production endpoints). SDK 2.x targets Daytona Cloud. For a custom API URL,"\n'
    '                + " stay on SDK 0.x.",'
)


def _rewrite_dotenv_remedy(root: Path) -> None:
    path = root / "_utils" / "env.py"
    text = path.read_text()
    if text.count(_DOTENV_REMEDY) != 1:
        raise SystemExit(f"bundle-v0: v0's dotenv warning text changed in {path}; update _DOTENV_REMEDY")
    path.write_text(text.replace(_DOTENV_REMEDY, _DOTENV_REMEDY_V0))


def _share_errors(root: Path) -> list[str]:
    """Make v0's error classes inherit the shared root markers.

    Anchored rewrite of each ``class <Name>(<bases>):`` line in
    ``common/errors.py``: ``DaytonaError(Exception)`` becomes
    ``DaytonaError(_shared_errors.DaytonaError)`` and every other category gains
    its marker as a second base. Fails if a class is missing or defined more
    than once, like the alias check.
    """
    path = root / V0_ERRORS_MODULE
    if not path.is_file():
        raise SystemExit(f"bundle-v0: {V0_ERRORS_MODULE} not found in the v0 package")
    text = path.read_text()
    problems: list[str] = []
    for marker, v0_name in V0_SHARED_ERRORS.items():
        pattern = re.compile(rf"^class {v0_name}\(([^)]*)\):$", re.MULTILINE)
        matches = pattern.findall(text)
        if len(matches) != 1:
            problems.append(f"{v0_name} ({len(matches)} definitions)")
            continue
        bases = matches[0].strip()
        if "_shared_errors." in bases:
            problems.append(f"{v0_name} already inherits a root marker ({bases})")
            continue
        if v0_name == "DaytonaError":
            if bases != "Exception":
                problems.append(f"DaytonaError has bases {bases!r}, expected 'Exception'")
                continue
            new_bases = f"_shared_errors.{marker}"
        else:
            new_bases = f"{bases}, _shared_errors.{marker}"
        text = pattern.sub(lambda _m, b=new_bases, n=v0_name: f"class {n}({b}):", text, count=1)
    if problems:
        raise SystemExit(
            "bundle-v0: the v0 errors module no longer matches the shared hierarchy "
            f"(daytona/_errors.py): {', '.join(problems)}"
        )
    # common/errors.py -> daytona.v0.common.errors: three dots reach `daytona`.
    first_class = re.search(r"^class DaytonaError\(", text, re.MULTILINE).start()  # type: ignore[union-attr]
    text = (
        text[:first_class]
        + "from ... import _errors as _shared_errors  # added by scripts/bundle-v0.sh\n\n\n"
        + text[first_class:]
    )
    _check_shared_errors(text)
    path.write_text(text)
    return list(V0_SHARED_ERRORS.values())


def _check_shared_errors(text: str) -> None:
    """Parse the rewritten module and assert the rewrite is what the root
    hierarchy needs: the relative import of daytona._errors, and each class
    inheriting its marker exactly once (a duplicate base would only fail at
    import time). Fails the bundle rather than a later test."""
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise SystemExit(f"bundle-v0: the rewritten v0 errors module does not parse: {error}") from error
    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 3
        and node.module is None
        and any(alias.name == "_errors" and alias.asname == "_shared_errors" for alias in node.names)
    ]
    if len(imports) != 1:
        raise SystemExit(
            "bundle-v0: the rewritten v0 errors module lacks exactly one `from ... import _errors`"
        )
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    problems: list[str] = []
    for marker, name in V0_SHARED_ERRORS.items():
        node = classes.get(name)
        if node is None:
            problems.append(f"{name} missing")
            continue
        bases = [ast.unparse(base) for base in node.bases]
        if bases.count(f"_shared_errors.{marker}") != 1 or len(bases) != len(set(bases)):
            problems.append(f"{name} has bases {bases}")
        if name == "DaytonaError" and bases != ["_shared_errors.DaytonaError"]:
            problems.append(f"DaytonaError has bases {bases}, expected only the root marker")
    if problems:
        raise SystemExit(f"bundle-v0: the v0 errors rewrite is inconsistent: {'; '.join(problems)}")


def _hide_clients(root: Path) -> dict[str, tuple[str, str]]:
    """Take the client classes out of v0's public surface.

    Anchored line rewrites of ``daytona/v0/__init__.py``, each required to
    match exactly once: the ``__all__`` entry is dropped, the ``TYPE_CHECKING``
    import binds the private name, and the lazy ``_DYNAMIC_IMPORTS`` entry is
    removed (v0's own ``__getattr__`` would otherwise resolve and cache the
    public name). Returns private name -> (submodule, class name) for the
    appended ``__getattr__``.
    """
    init = root / "__init__.py"
    text = init.read_text()
    hidden: dict[str, tuple[str, str]] = {}
    problems: list[str] = []

    def replace_once(pattern: str, replacement: str, what: str) -> re.Match[str] | None:
        nonlocal text
        matches = list(re.finditer(pattern, text, re.MULTILINE))
        if len(matches) != 1:
            problems.append(f"{what} ({len(matches)} matches)")
            return None
        text = text[: matches[0].start()] + replacement + text[matches[0].end() :]
        return matches[0]

    clients = _v2_list("HIDDEN_CLIENTS")
    for name in clients:
        private = f"_{name}"
        replace_once(rf'^    "{name}",\n', "", f"__all__ entry {name!r}")
        # `from ._sync.daytona import Daytona`, or `DaytonaConfig,` in a parenthesized import.
        single = list(re.finditer(rf"^(    from \.[\w.]+ import ){name}\n", text, re.MULTILINE))
        listed = list(re.finditer(rf"^(        ){name},\n", text, re.MULTILINE))
        if len(single) + len(listed) != 1:
            problems.append(f"TYPE_CHECKING import of {name} ({len(single) + len(listed)} matches)")
        else:
            match = (single or listed)[0]
            suffix = "\n" if single else ",\n"
            text = (
                text[: match.start()] + f"{match.group(1)}{name} as {private}{suffix}" + text[match.end() :]
            )
        entry = replace_once(rf'^    "{name}": "([\w.]+)",\n', "", f"_DYNAMIC_IMPORTS entry {name!r}")
        if entry is not None:
            hidden[private] = (entry.group(1), name)
    if problems:
        raise SystemExit(
            f"bundle-v0: cannot hide v0's client classes in {init}: {', '.join(problems)}; "
            "update _hide_clients for this v0 release"
        )
    init.write_text(text)
    if set(clients) & _public_names(init):
        raise SystemExit(f"bundle-v0: {init} still exports a hidden client class")
    return hidden


def _public_names(init: Path) -> set[str]:
    tree = ast.parse(init.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "__all__" for t in node.targets):
            return set(ast.literal_eval(node.value))
    raise SystemExit(f"bundle-v0: {init} has no literal __all__")


def _v2_list(name: str) -> tuple[str, ...]:
    """A literal list or tuple assigned at the top level of daytona/v2/_exports.py."""
    for node in ast.parse(V2_EXPORTS.read_text()).body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return tuple(ast.literal_eval(node.value))
    raise SystemExit(f"bundle-v0: {V2_EXPORTS} has no literal {name}")


def _v2_names() -> tuple[set[str], tuple[str, ...]]:
    return set(_v2_list("ROOT_EXPORTS")), _v2_list("SHARED_WITH_V0")


def _add_aliases(root: Path, hidden: dict[str, tuple[str, str]]) -> list[str]:
    init = root / "__init__.py"
    v0_names = _public_names(init)
    v2_names, declared = _v2_names()
    shared = sorted(v0_names & v2_names)
    if set(shared) != set(declared):
        # Fail rather than ship a v0 namespace whose V0 aliases no longer
        # mirror v2's committed V2 aliases.
        raise SystemExit(
            "bundle-v0: daytona.v2._exports.SHARED_WITH_V0 does not match this v0 release "
            f"(missing: {sorted(set(shared) - set(declared))}, stale: {sorted(set(declared) - set(shared))}); "
            "update SHARED_WITH_V0 and the V2 aliases in daytona/v2/__init__.py"
        )
    aliases = {f"{name}V0": name for name in shared}
    # v0's __init__ resolves its exports lazily through a module __getattr__,
    # so the aliases and the hidden clients resolve through it too (and only
    # static checkers see the aliases as plain assignments).
    lines = [
        "",
        "# --- added by scripts/bundle-v0.sh: version-suffixed aliases for the",
        "# names daytona.v2 exports too, and the",
        "# client classes under the private names only the root's `.v0` reads.",
        "import importlib as _bundle_importlib",
        "import sys as _bundle_sys",
        "from typing import TYPE_CHECKING as _BUNDLE_TYPE_CHECKING",
        "",
        f"_BUNDLE_ALIASES = {aliases!r}",
        f"_BUNDLE_HIDDEN = {hidden!r}",
        # The public names and their would-be `…V0` aliases: both get the guidance.
        f"_BUNDLE_HIDDEN_PUBLIC = frozenset({sorted(n for _, name in hidden.values() for n in (name, name + 'V0'))!r})",
        '_bundle_previous_getattr = globals().get("__getattr__")',
        "",
        "",
        "def __getattr__(attr_name: str) -> object:",
        "    if attr_name in _BUNDLE_HIDDEN_PUBLIC:",
        "        # An ImportError, not an AttributeError: `from daytona.v0 import Daytona`",
        "        # would replace the latter with a generic message (daytona.__getattr__",
        "        # does the same for names moved off the root).",
        "        from daytona._client import _client_not_exported",
        "",
        "        raise ImportError(_client_not_exported('v0', attr_name), name=__name__)",
        "    target = _BUNDLE_ALIASES.get(attr_name)",
        "    if target is not None:",
        "        return getattr(_bundle_sys.modules[__name__], target)",
        "    hidden = _BUNDLE_HIDDEN.get(attr_name)",
        "    if hidden is None and _bundle_previous_getattr is None:",
        '        raise AttributeError(f"module {__name__!r} has no attribute {attr_name!r}")',
        "    try:",
        "        if hidden is None:",
        "            return _bundle_previous_getattr(attr_name)",
        '        value = getattr(_bundle_importlib.import_module(f".{hidden[0]}", __name__), hidden[1])',
        "        globals()[attr_name] = value",
        "        return value",
        "    except ModuleNotFoundError as _bundle_error:",
        "        # v0 resolves exports lazily, so a dependency the v0-sdk extra",
        "        # installs is first imported here: name the extra.",
        "        if (_bundle_error.name or '').startswith(__name__ + '.'):",
        "            raise",
        "        from daytona._client import _V0_EXTRA_HINT",
        "",
        "        raise ImportError(",
        '            f"{_V0_EXTRA_HINT} (importing the v0 namespace failed on {_bundle_error.name!r}: {_bundle_error})",',
        "            name=__name__,",
        "        ) from _bundle_error",
        "",
        "",
        "if _BUNDLE_TYPE_CHECKING:",
        *(f"    {alias} = {name}" for alias, name in aliases.items()),
        "",
        f"__all__ += {list(aliases)!r}",
        "",
    ]
    with init.open("a") as handle:
        handle.write("\n".join(lines))
    return shared


def _fill_extra(sdk_python: Path, version: str) -> list[str]:
    # Imported here: the SDK test-suite imports this module on Python 3.10,
    # which has no tomllib; the bundle itself runs on 3.11+.
    import tomllib

    project = tomllib.loads((sdk_python / "pyproject.toml").read_text())["project"]
    requirements = [
        *project["dependencies"],
        *(f"{name}=={version}" for name in API_CLIENTS),
    ]
    rendered = "v0-sdk = [\n" + "".join(f'  "{req}",\n' for req in requirements) + "]"
    _set_extra(rendered)
    return requirements


def _set_extra(rendered: str) -> None:
    text = PYPROJECT.read_text()
    new, n = _EXTRA.subn(lambda _m: rendered, text, count=1)
    if n != 1:
        raise SystemExit(f"bundle-v0: no `v0-sdk = [...]` extra found in {PYPROJECT}")
    PYPROJECT.write_text(new)


def clean() -> None:
    shutil.rmtree(DEST, ignore_errors=True)
    _set_extra("v0-sdk = []")
    print(f"bundle-v0: removed {DEST} and emptied the v0-sdk extra")


def bundle(sdk_python: Path, version: str, tag: str) -> None:
    source = sdk_python / "src" / "daytona"
    if not (source / "__init__.py").is_file():
        raise SystemExit(f"bundle-v0: {source} is not the v0 Python package")
    # Refuse symlinks: copytree would dereference them and copy bytes from
    # outside the vendored release into the wheel.
    links = sorted(str(path.relative_to(source)) for path in source.rglob("*") if path.is_symlink())
    if links:
        raise SystemExit(
            f"bundle-v0: {source} contains symlinks, refusing to vendor them: {', '.join(links)}"
        )
    shutil.rmtree(DEST, ignore_errors=True)
    shutil.copytree(source, DEST, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # The v0 code ships under its own license terms: carry the 0.x SDK's
    # LICENSE and the repository's NOTICE into the bundle (wheel and sdist).
    for name, origin in (("LICENSE", sdk_python / "LICENSE"), ("NOTICE", sdk_python.parent / "NOTICE")):
        if not origin.is_file():
            raise SystemExit(f"bundle-v0: {origin} is missing; the bundle must carry v0's {name}")
        shutil.copyfile(origin, DEST / name)
    (DEST / "_bundle.py").write_text(
        '"""Provenance of this bundle. GENERATED by scripts/bundle-v0.sh; never committed."""\n\n'
        f'BUNDLED_FROM = "daytona/clients@{tag}"\n'
        f'BUNDLED_VERSION = "{version}"\n\n\n'
        "def bundled_version(_distribution: str) -> str:\n"
        '    """Stand-in for importlib.metadata.version: the bundled v0 release."""\n'
        "    return BUNDLED_VERSION\n"
    )
    imports = _rewrite_imports(DEST)
    pinned = _pin_sdk_version(DEST, version)
    hidden = _hide_clients(DEST)
    shared = _add_aliases(DEST, hidden)
    shared_errors = _share_errors(DEST)
    _rewrite_dotenv_remedy(DEST)
    requirements = _fill_extra(sdk_python, version)
    print(f"bundle-v0: daytona/clients@{tag} -> {DEST.relative_to(PYTHON)}")
    print(f"bundle-v0:   rewrote {imports} absolute daytona imports to daytona.v0")
    print(f"bundle-v0:   SDK-version header pinned to {version} in {pinned}")
    print(f"bundle-v0:   client classes hidden behind {', '.join(sorted(hidden))}")
    print(f"bundle-v0:   V0 aliases: {', '.join(shared)}")
    print(f"bundle-v0:   shared root errors inherited by {len(shared_errors)} v0 classes")
    print(f"bundle-v0:   v0-sdk extra: {len(requirements)} requirements")


def main(argv: list[str]) -> None:
    if argv == ["--clean"]:
        clean()
        return
    if len(argv) != 3:
        raise SystemExit(__doc__)
    sdk_python, version, tag = argv
    bundle(Path(sdk_python), version, tag)


if __name__ == "__main__":
    main(sys.argv[1:])
