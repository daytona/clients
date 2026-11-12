#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Upgrade the pinned 0.x SDK's own examples with the daytona-upgrade tools and
# type-check the result against SDK 2.x. Proves, on real 0.x code, that the tools leave nothing
# a type checker can see as a 0.x-only call or import, and that a second run
# changes nothing.
#
# Usage: check-examples.sh --src <daytona/clients checkout> [--py-wheel <daytona 2.x wheel>] \
#          [--ts-tarball <@daytona/sdk 2.x .tgz>] [--work <dir>]
# Checks each language whose SDK artifact is given (at least one). The artifacts
# must carry the v0 bundle (scripts/bundle-v0.sh).
# Needs git and python3, plus node/npm for TypeScript and uv for Python, on PATH.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
upgrade=$(cd "$here/.." && pwd)
src='' wheel='' tarball='' work=''
while [ $# -gt 0 ]; do
  case "$1" in
    --src) src=$2; shift 2 ;;
    --py-wheel) wheel=${2:?--py-wheel needs a wheel path}; shift 2 ;;
    --ts-tarball) tarball=${2:?--ts-tarball needs a tarball path}; shift 2 ;;
    --work) work=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$src" ] && { [ -n "$wheel" ] || [ -n "$tarball" ]; } || { echo "usage: $0 --src DIR [--py-wheel WHL] [--ts-tarball TGZ] [--work DIR]" >&2; exit 2; }
src=$(cd "$src" && pwd)
[ -z "$wheel" ] || wheel=$(cd "$(dirname "$wheel")" && pwd)/$(basename "$wheel")
[ -z "$tarball" ] || tarball=$(cd "$(dirname "$tarball")" && pwd)/$(basename "$tarball")
# The 0.x release the examples come from: the pin SDK 2.x bundles as .v0.
pinned=$(sed -n '1s/^v//p' "$upgrade/../scripts/v0-sdk-version" | tr -d '[:space:]')
work=${work:-$(mktemp -d)}
mkdir -p "$work"
work=$(cd "$work" && pwd)

commit() { git -C "$1" add -A && git -C "$1" -c user.email=ci@daytona.invalid -c user.name=ci commit -qm "$2"; }
fresh_repo() { rm -rf "$1" && mkdir -p "$1" && git -C "$1" init -q; }
# The second run's report must not ask for the dependency it just wrote (read
# with python3, which both language checks have).
target_kept() { # report-json manifest label
  if ! python3 -c '
import json, sys
bad = [item for item in json.load(sys.stdin)["items"] if item["kind"] == "manual-dependency" and item["file"] == sys.argv[1]]
if bad:
    print(json.dumps(bad), file=sys.stderr)
    sys.exit(1)
' "$2" <<<"$1"; then
    echo "FAIL: a second $3 run reports the dependency it wrote as a manual item" >&2
    exit 1
  fi
}
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
(cd "$upgrade/typescript" && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null)
node "$upgrade/typescript/dist/cli.js" "$ts" --yes
grep -q '"@daytona/sdk": "^2.0.0"' "$ts/package.json" || { echo "FAIL: package.json not bumped" >&2; exit 1; }
commit "$ts" "upgraded"
ts_report=$(node "$upgrade/typescript/dist/cli.js" "$ts" --yes --json)
target_kept "$ts_report" package.json "TypeScript upgrade"
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
(cd "$upgrade/python" && uv sync --locked -q && uv run -q daytona-upgrade "$py" --yes)
grep -qx 'daytona\[v0-sdk\]>=2,<3' "$py/requirements.txt" || { echo "FAIL: requirements.txt not bumped" >&2; exit 1; }
commit "$py" "upgraded"
py_report=$(cd "$upgrade/python" && uv run -q daytona-upgrade "$py" --yes --json)
target_kept "$py_report" requirements.txt "Python upgrade"
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
