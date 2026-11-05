#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Upgrade the pinned 0.x SDK's own examples with the daytona-upgrade tools and
# type-check the result against SDK 2.x. Proves, on real 0.x code, that the tools leave nothing
# a type checker can see as a 0.x-only call or import, and that a second run
# changes nothing.
#
# Usage: check-examples.sh --src <daytona/clients checkout> [--py-wheel <daytona 2.x wheel>] \
#          [--ts-tarball <@daytona/sdk 2.x .tgz>] [--py-tool <daytona-upgrade wheel>] \
#          [--ts-tool <@daytona/upgrade .tgz>] [--work <dir>]
# Checks each language whose SDK artifact is given (at least one). The artifacts
# must carry the v0 bundle (scripts/bundle-v0.sh). The tools run from source
# unless --py-tool / --ts-tool name a packed tool (the publish run checks the
# artifacts it publishes); a packed tool writes the exact SDK release it was
# published with, a source build the mapping's 2.x range.
# Needs git, plus node/npm for TypeScript and python3 with uv for Python, on PATH.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
upgrade=$(cd "$here/.." && pwd)
src='' wheel='' tarball='' work='' py_tool='' ts_tool=''
while [ $# -gt 0 ]; do
  case "$1" in
    --src) src=$2; shift 2 ;;
    --py-wheel) wheel=${2:?--py-wheel needs a wheel path}; shift 2 ;;
    --ts-tarball) tarball=${2:?--ts-tarball needs a tarball path}; shift 2 ;;
    --py-tool) py_tool=${2:?--py-tool needs a wheel path}; shift 2 ;;
    --ts-tool) ts_tool=${2:?--ts-tool needs a tarball path}; shift 2 ;;
    --work) work=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$src" ] && { [ -n "$wheel" ] || [ -n "$tarball" ]; } || { echo "usage: $0 --src DIR [--py-wheel WHL] [--ts-tarball TGZ] [--py-tool WHL] [--ts-tool TGZ] [--work DIR]" >&2; exit 2; }
src=$(cd "$src" && pwd)
[ -z "$wheel" ] || wheel=$(cd "$(dirname "$wheel")" && pwd)/$(basename "$wheel")
[ -z "$tarball" ] || tarball=$(cd "$(dirname "$tarball")" && pwd)/$(basename "$tarball")
[ -z "$py_tool" ] || py_tool=$(cd "$(dirname "$py_tool")" && pwd)/$(basename "$py_tool")
[ -z "$ts_tool" ] || ts_tool=$(cd "$(dirname "$ts_tool")" && pwd)/$(basename "$ts_tool")
# The 0.x release the examples come from: the pin SDK 2.x bundles as .v0.
pinned=$(sed -n '1s/^v//p' "$upgrade/../scripts/v0-sdk-version" | tr -d '[:space:]')
work=${work:-$(mktemp -d)}
mkdir -p "$work"
work=$(cd "$work" && pwd)

commit() { git -C "$1" add -A && git -C "$1" -c user.email=ci@daytona.invalid -c user.name=ci commit -qm "$2"; }
fresh_repo() { rm -rf "$1" && mkdir -p "$1" && git -C "$1" init -q; }
idempotent() {
  if [ -n "$(git -C "$1" status --porcelain)" ]; then
    echo "FAIL: a second $2 run changed files:" >&2
    git -C "$1" status --short >&2
    exit 1
  fi
}

# ── TypeScript ──────────────────────────────────────────────────────────────
if [ -n "$tarball" ]; then
ts="$work/ts"
fresh_repo "$ts"
cp -r "$src/examples/typescript/." "$ts/"
cat >"$ts/package.json" <<JSON
{ "name": "upgrade-examples", "private": true, "dependencies": { "@daytona/sdk": "$pinned" } }
JSON
printf 'node_modules\n' >"$ts/.gitignore"
commit "$ts" "0.x examples"
if [ -n "$ts_tool" ]; then
  rm -rf "$work/ts-tool" && mkdir -p "$work/ts-tool"
  (cd "$work/ts-tool" && npm init -y >/dev/null && npm install --no-audit --no-fund --ignore-scripts "$ts_tool" >/dev/null)
  ts_cli="$work/ts-tool/node_modules/@daytona/upgrade/dist/cli.js"
else
  (cd "$upgrade/typescript" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null)
  ts_cli="$upgrade/typescript/dist/cli.js"
fi
node "$ts_cli" "$ts" --yes
# The tool's target: its own release when packed, else the mapping's range.
ts_range=$(node -e '
  const tool = require(process.argv[1]).version;
  const m = /^([1-9]\d*)\.\d+\.\d+(-.+)?$/.exec(tool);
  console.log(m ? (m[2] ? tool : `^${tool}`) : require(process.argv[2]).packages.ts.range);
' "$(dirname "$ts_cli")/../package.json" "$upgrade/mapping/mapping.json")
grep -qF "\"@daytona/sdk\": \"$ts_range\"" "$ts/package.json" || { echo "FAIL: package.json not bumped to $ts_range" >&2; exit 1; }
commit "$ts" "upgraded"
node "$ts_cli" "$ts" --yes >/dev/null
idempotent "$ts" "TypeScript upgrade"
(
  cd "$ts"
  npm install --no-audit --no-fund --no-save "$tarball" typescript@5 @types/node@22 >/dev/null
  for resolution in node bundler; do
    module=commonjs
    [ "$resolution" = bundler ] && module=esnext
    cat >tsconfig.json <<JSON
{ "compilerOptions": { "target": "es2022", "module": "$module", "moduleResolution": "$resolution",
  "strict": false, "esModuleInterop": true, "skipLibCheck": true, "noEmit": true,
  "lib": ["es2022", "dom", "esnext.disposable"] }, "include": ["**/*.ts", "**/*.tsx"], "exclude": ["node_modules"] }
JSON
    echo "tsc (moduleResolution $resolution) on the upgraded TypeScript examples"
    npx tsc -p tsconfig.json
  done
  rm tsconfig.json
)
fi

# ── Python (and the Jupyter notebook) ───────────────────────────────────────
if [ -n "$wheel" ]; then
py="$work/py"
fresh_repo "$py"
cp -r "$src/examples/python" "$py/python"
cp -r "$src/examples/jupyter" "$py/jupyter"
printf 'daytona==%s\n' "$pinned" >"$py/requirements.txt"
commit "$py" "0.x examples"
py_upgrade() {
  if [ -n "$py_tool" ]; then uvx -q --python 3.12 --from "$py_tool" daytona-upgrade "$@"
  else (cd "$upgrade/python" && uv sync --locked -q && uv run -q daytona-upgrade "$@"); fi
}
py_upgrade "$py" --yes
if [ -n "$py_tool" ]; then
  py_version=$(basename "$py_tool" | cut -d- -f2)
  case "$py_version" in
    0.*) py_requirement='daytona[v0-sdk]>=2,<3' ;;
    *[a-z]*) py_requirement="daytona[v0-sdk]==$py_version" ;;
    *) py_requirement="daytona[v0-sdk]>=$py_version,<$(( ${py_version%%.*} + 1 ))" ;;
  esac
else
  py_requirement='daytona[v0-sdk]>=2,<3'
fi
grep -qxF "$py_requirement" "$py/requirements.txt" || { echo "FAIL: requirements.txt not bumped to $py_requirement" >&2; exit 1; }
commit "$py" "upgraded"
py_upgrade "$py" --yes >/dev/null
idempotent "$py" "Python upgrade"
venv="$work/venv"
rm -rf "$venv"
uv venv -q --python 3.12 "$venv"
uv pip install -q -p "$venv/bin/python" "${wheel}[v0-sdk]" mypy
# The notebook's code cells, as one module each notebook (magics dropped), so
# the same mypy pass covers the migrated notebook too.
mkdir -p "$py/notebook-cells"
python3 - "$py" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
for nb in root.glob("jupyter/**/*.ipynb"):
    cells = json.loads(nb.read_text(encoding="utf-8"))["cells"]
    code = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        src = cell["source"] if isinstance(cell["source"], str) else "".join(cell["source"])
        code.append("\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("%", "!"))))
    (root / "notebook-cells" / f"notebook_{nb.stem}.py").write_text("\n\n".join(code) + "\n", encoding="utf-8")
PY
echo "mypy on the upgraded Python examples and notebook cells (migration errors only)"
fail=0
while IFS= read -r -d '' f; do
  # One file at a time: the examples reuse module names across directories.
  rc=0
  out=$("$venv/bin/python" -m mypy --ignore-missing-imports --no-error-summary --follow-imports=silent "$f" 2>&1) || rc=$?
  # mypy exits 1 for type errors (filtered below) and 2 when it could not check the file at all.
  if [ "$rc" -gt 1 ]; then printf 'mypy could not check %s:\n%s\n' "$f" "$out" >&2; fail=1; continue; fi
  hits=$(printf '%s\n' "$out" | grep -E 'error: .*("Daytona"|"AsyncDaytona"|Module "daytona|daytona\.v0|has no attribute "(create|get|list|start|stop|delete|volume|snapshot|secret|warm_pool)")' || true)
  if [ -n "$hits" ]; then printf '%s\n' "$hits" >&2; fail=1; fi
done < <(find "$py/python" "$py/notebook-cells" -name '*.py' -print0)
# --ignore-missing-imports would hide a leftover import of the old package name.
if ! python3 - "$py/python" "$py/notebook-cells" <<'PY'
import ast, pathlib, sys
bad = []
for root in sys.argv[1:]:
    for path in pathlib.Path(root).rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            bad += [f"{path}:{node.lineno}: {n}" for n in names if n.split(".")[0] == "daytona_sdk"]
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
then
  echo "FAIL: upgraded Python examples still import daytona_sdk" >&2; fail=1
fi
[ "$fail" -eq 0 ] || { echo "FAIL: upgraded Python examples still use 0.x-only names" >&2; exit 1; }
fi
echo "OK: the upgraded pinned 0.x examples type-check against SDK 2.x (${tarball:+TypeScript}${tarball:+${wheel:+, }}${wheel:+Python})"
