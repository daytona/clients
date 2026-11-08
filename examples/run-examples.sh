#!/usr/bin/env bash
# Run the Daytona SDK examples against a Daytona deployment.
#
#   DAYTONA_API_KEY=dtn_... ./run-examples.sh            # all examples, both languages
#   ... ./run-examples.sh ts       # typescript only
#   ... ./run-examples.sh py       # python only
#
# Env (see each example's header): DAYTONA_V2_API_URL (default https://api.daytona.com),
# DAYTONA_API_KEY (required), DAYTONA_IMAGE (default python:3.12-slim).
#
# Notes:
#   - `dynamic-build`, `dockerfile-sandbox` and `build-logs` build a
#     Dockerfile, so the deployment must support image builds. It is
#     slower than the others: the first create waits for a real build.
#   - Volume examples are opt-in with DAYTONA_RUN_VOLUMES=1.
#     volumes_share.{ts,py} are standalone persistence round-trip examples.
#   - Without API access you can still validate the examples statically:
#       ./run-examples.sh check     # type checks + offline tests against a fake client
set -uo pipefail
cd "$(dirname "$0")"
WHICH="${1:-both}"

PY_BIN="../sdk-python/.venv/bin/python"
[[ -x "$PY_BIN" ]] || PY_BIN="python3"

TS_EXAMPLES=(exec-command file-operations git lifecycle snapshots dynamic-build dockerfile-sandbox build-logs pagination secrets-env)
PY_EXAMPLES=(exec_command file_operations git_ops lifecycle snapshots dynamic_build dockerfile_sandbox build_logs pagination secrets_env)
if [[ "${DAYTONA_RUN_VOLUMES:-}" == "1" ]]; then
  TS_EXAMPLES+=(volumes)
  PY_EXAMPLES+=(volumes)
fi

pass=0 fail=0
run() { # <label> <cmd...>
  local label="$1"; shift
  echo; echo "########## $label ##########"
  if "$@"; then echo ">>> OK: $label"; pass=$((pass + 1))
  else echo ">>> FAIL: $label"; fail=$((fail + 1)); fi
}

check() {
  echo "== static checks + offline tests (no API access needed) =="
  (cd . && npm install --no-audit --no-fund >/dev/null 2>&1)
  run "tsc --noEmit" npx tsc -p tsconfig.json --noEmit
  run "ts offline tests" node --import tsx --test \
    typescript/examples.test.ts typescript/follow-build-logs/output.test.ts \
    typescript/test-support/fake-build-environment.test.ts
  # mypy and pytest come from the SDK's dev extras (pip install -e "../sdk-python[dev]").
  run "mypy --strict" env MYPYPATH=python "$PY_BIN" -m mypy --strict python volumes_share.py
  run "python offline tests" "$PY_BIN" -m pytest -q -p no:cacheprovider python/test_examples.py
}

run_ts() {
  npm install --no-audit --no-fund >/dev/null 2>&1
  for ex in "${TS_EXAMPLES[@]}"; do run "ts/$ex" npx tsx "typescript/$ex/index.ts"; done
}

run_py() {
  for ex in "${PY_EXAMPLES[@]}"; do run "py/$ex" env PYTHONPATH=python "$PY_BIN" "python/$ex.py"; done
}

case "$WHICH" in
  check) check ;;
  ts) run_ts ;;
  py) run_py ;;
  both) run_ts; run_py ;;
  *) echo "usage: $0 [both|ts|py|check]" >&2; exit 2 ;;
esac

echo; echo "== summary: $pass passed, $fail failed =="
exit "$((fail > 0 ? 1 : 0))"
