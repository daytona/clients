# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

"""Write the migration tool's public surface to upgrade/api-surface/daytona-upgrade.api.txt.

What scripts and CI jobs depend on, one entry per line, sorted:

- every object path of the --json report (upgrade/mapping/report.schema.json)
  with each property's type and whether it is always present;
- the listed values of each vocabulary (``kind``, ``refused.reason``, ``mode``,
  ``language``, ``suggestion.category``), read from ``examples`` or ``enum``;
- both tools' flags (name, shorthand, whether it takes a value), read from
  their argument parsers: the PyPI tool's argparse parser and the
  ``parseArgs`` options of the npm tool (upgrade/typescript/src/arguments.ts).

Run from upgrade/python, so the PyPI tool and its dependencies import:

    (cd upgrade/python && uv run python ../scripts/api_surface.py)           # regenerate
    (cd upgrade/python && uv run python ../scripts/api_surface.py --check)   # exit 1 when stale

Line format, shared with the SDK and CLI snapshots and read by
scripts/breaking/breaking_changes.py: ``<name>: <kind>[ <detail>]``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

UPGRADE = Path(__file__).resolve().parent.parent
SCHEMA = UPGRADE / "mapping" / "report.schema.json"
ARGUMENTS_TS = UPGRADE / "typescript" / "src" / "arguments.ts"
OUT = UPGRADE / "api-surface" / "daytona-upgrade.api.txt"
REGENERATE = "(cd upgrade/python && uv run python ../scripts/api_surface.py)"
JSON_TYPES = {
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
    type(None): "null",
}


def schema_type(node: dict[str, object]) -> str:
    if "const" in node:
        return f"const {json.dumps(node['const'])}"
    declared = node.get("type")
    if isinstance(declared, list):
        return " | ".join(str(t) for t in declared)
    if isinstance(declared, str):
        return declared
    values = node.get("enum") or node.get("examples")
    if isinstance(values, list) and values:
        return " | ".join(sorted({JSON_TYPES[type(v)] for v in values}))
    raise SystemExit(f"api_surface: no type for schema node {json.dumps(node)[:120]}")


def schema_lines(node: dict[str, object], path: str, lines: list[str]) -> None:
    properties = node.get("properties")
    if isinstance(properties, dict):
        required = set(node.get("required") or [])  # type: ignore[arg-type]
        for name, child in properties.items():
            child_path = f"{path}.{name}"
            presence = "required" if name in required else "optional"
            lines.append(f"{child_path}: field {schema_type(child)} {presence}")
            values = child.get("enum") if "enum" in child else child.get("examples")
            if isinstance(values, list) and ("enum" in child or "examples" in child):
                lines.extend(f"{child_path}: value {json.dumps(v)}" for v in values)
            schema_lines(child, child_path, lines)
    items = node.get("items")
    if isinstance(items, dict):
        if "type" in items or "properties" in items:
            lines.append(f"{path}[]: item {schema_type(items)}")
        schema_lines(items, f"{path}[]", lines)


def python_flags() -> list[str]:
    sys.path.insert(0, str(UPGRADE / "python" / "src"))
    from daytona_upgrade.cli import _parser

    lines = []
    for action in _parser()._actions:
        longs = [o for o in action.option_strings if o.startswith("--")]
        shorts = [o for o in action.option_strings if not o.startswith("--")]
        value = "switch" if action.nargs == 0 else "value"
        for flag in longs:
            short = f" {' '.join(shorts)}" if shorts else ""
            lines.append(f"pypi {flag}: flag {value}{short}")
        if not longs:
            # A short-only flag is listed under its short name.
            lines.extend(f"pypi {flag}: flag {value}" for flag in shorts)
    return lines


def matching_brace(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise SystemExit("api_surface: unbalanced braces in the parseArgs options")


def npm_flags() -> list[str]:
    source = ARGUMENTS_TS.read_text(encoding="utf-8")
    call = source.find("parseArgs(")
    options = source.find("options:", call)
    if call < 0 or options < 0:
        raise SystemExit(
            f"api_surface: no parseArgs({{ options }}) call in {ARGUMENTS_TS}"
        )
    open_at = source.index("{", options)
    body = source[open_at + 1 : matching_brace(source, open_at)]
    entry = re.compile(r"""(?:'([\w-]+)'|([A-Za-z_]\w*))\s*:\s*\{([^{}]*)\}\s*,?""")
    lines = []
    leftover = entry.sub("", body)
    if leftover.strip():
        raise SystemExit(
            f"api_surface: unrecognised parseArgs options in {ARGUMENTS_TS}: {leftover.strip()[:120]}"
        )
    for match in entry.finditer(body):
        name = match[1] or match[2]
        fields: list[tuple[str, str]] = re.findall(
            r"(\w+)\s*:\s*('[^']*'|true|false)", match[3]
        )
        spec = {k: v.strip("'") for k, v in fields}
        unknown = set(spec) - {"type", "short", "multiple"}
        if unknown or spec.get("type") not in ("boolean", "string"):
            raise SystemExit(
                f"api_surface: unrecognised parseArgs option {name}: {match[3].strip()}"
            )
        value = "switch" if spec["type"] == "boolean" else "value"
        if spec.get("multiple") == "true":
            value += " repeatable"
        short = f" -{spec['short']}" if "short" in spec else ""
        lines.append(f"npm --{name}: flag {value}{short}")
    return lines


def render() -> str:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    lines: list[str] = [f"report: {schema_type(schema)}"]
    schema_lines(schema, "report", lines)
    lines += python_flags() + npm_flags()

    def key(line: str) -> tuple[str, str]:
        name, _, rest = line.partition(": ")
        return name, rest

    header = [
        "# Public surface of the migration tool (daytona-upgrade, npm and PyPI): the --json report and the flags.",
        f"# Generated by upgrade/scripts/api_surface.py; do not edit. Regenerate: {REGENERATE}.",
    ]
    return "\n".join([*header, *sorted(lines, key=key)]) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="exit 1 when the committed file is stale"
    )
    check = parser.parse_args(argv).check
    text = render()
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if current == text:
        return 0
    if not check:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT.relative_to(UPGRADE.parent)}")
        return 0
    print(
        f"{OUT.relative_to(UPGRADE.parent)} is stale: the migration tool's public surface changed.",
        file=sys.stderr,
    )
    before, after = set(current.splitlines()), set(text.splitlines())
    for line in current.splitlines():
        if line not in after:
            print(f"  - {line}", file=sys.stderr)
    for line in text.splitlines():
        if line not in before:
            print(f"  + {line}", file=sys.stderr)
    print(f"Regenerate with `{REGENERATE}` and commit the result.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
