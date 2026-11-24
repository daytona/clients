#!/usr/bin/env bash
# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0

# Live e2e for the Daytona SDKs (Python, TypeScript, Go) against a Daytona
# deployment.
#
#   DAYTONA_API_KEY=dtn_... ./e2e/run.sh          # python + typescript
#   DAYTONA_API_KEY=dtn_... ./e2e/run.sh py       # python only
#   DAYTONA_API_KEY=dtn_... ./e2e/run.sh ts       # typescript only
#   DAYTONA_API_KEY=dtn_... ./e2e/run.sh go       # go only (sdk-go/e2e, build tag e2e)
#   DAYTONA_API_KEY=dtn_... ./e2e/run.sh all      # python + typescript + go
#
# Env (see e2e/e2e.py / e2e/e2e.ts headers): DAYTONA_V2_API_URL, DAYTONA_API_KEY (required),
# SMOKE_IMAGE, DAYTONA_SKIP_TOOLBOX, DAYTONA_SKIP_DOCKERFILE
# (deployments without image builds), DAYTONA_KEEP.
set -euo pipefail

cd "$(dirname "$0")/.."
WHICH="${1:-both}"

if [[ -z "${DAYTONA_API_KEY:-}" ]]; then
  echo "DAYTONA_API_KEY is required (create a project API key in the Daytona dashboard)" >&2
  exit 2
fi

run_py() {
  echo "== python SDK e2e =="
  if command -v uv >/dev/null 2>&1; then
    (cd sdk-python && uv venv --allow-existing .venv >/dev/null 2>&1 || true
     uv pip install -p .venv -q -e . >/dev/null)
    sdk-python/.venv/bin/python e2e/e2e.py
  else
    (cd sdk-python && pip install -q -e . >/dev/null)
    python3 e2e/e2e.py
  fi
}

run_ts() {
  echo "== typescript SDK e2e =="
  (cd sdk-typescript && npm install --no-audit --no-fund >/dev/null 2>&1)
  (cd sdk-typescript && npx --yes tsx ../e2e/e2e.ts)
}

run_go() {
  echo "== go SDK e2e =="
  (cd sdk-go && go test -tags e2e -count=1 -timeout 25m ./e2e/...)
}

rc=0
case "$WHICH" in
  py) run_py || rc=$? ;;
  ts) run_ts || rc=$? ;;
  go) run_go || rc=$? ;;
  both) run_py || rc=$?; run_ts || rc=$? ;;
  all) run_py || rc=$?; run_ts || rc=$?; run_go || rc=$? ;;
  *) echo "usage: $0 [py|ts|go|both|all]" >&2; exit 2 ;;
esac
exit "$rc"
