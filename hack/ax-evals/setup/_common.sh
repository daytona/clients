# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Shared preamble for every 2027.dev AX eval setup script. sync.py prepends it to
# setup/<lang>.sh before uploading the result as the prompt's setupCommand.
#
# 2027 does not substitute template variables into setupCommand, but it does expose
# the run's urlMap to setup as URL_MAP ("host=upstream"). The workflow encodes the
# commit under test as {"git-ref.invalid": "https://<sha>.invalid"}; runs started
# from the 2027 dashboard without that mapping test main.
set -euo pipefail

T0=$(date +%s)
t() { echo "[t+$(($(date +%s) - T0))s] $*"; }
# Retry network-bound steps: a transient registry/proxy error otherwise fails the run.
retry() {
  local attempt
  for attempt in 1 2 3; do
    "$@" && return 0
    echo "attempt ${attempt} failed: $*" >&2
    sleep $((attempt * 10))
  done
  return 1
}

REF=$(printf '%s\n' "${URL_MAP:-}" | tr ', ;' '\n\n\n' |
  sed -n 's#^git-ref\.invalid=https\{0,1\}://\([0-9a-f]\{7,40\}\)\.invalid/\{0,1\}$#\1#p' | head -n 1)
REF=${REF:-main}

t "clone daytona/clients at ${REF}"
git clone --quiet --filter=blob:none https://github.com/daytona/clients.git /opt/clients
cd /opt/clients
git fetch --quiet origin "$REF"
git checkout --quiet --detach FETCH_HEAD
echo "build under test: $(git log --oneline -1)"

install_go() {
  if ! command -v go >/dev/null || ! go version | grep -q 'go1\.2[5-9]'; then
    t "install go"
    curl -fsSL --retry 3 https://go.dev/dl/go1.25.5.linux-amd64.tar.gz | tar -C /usr/local -xz
    ln -sf /usr/local/go/bin/go /usr/local/bin/go
  fi
  go version
}
