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

REF=$(printf '%s\n' "${URL_MAP:-}" | tr ', ;' '\n\n\n' | tr 'A-F' 'a-f' |
  sed -n 's#^git-ref\.invalid=https\{0,1\}://\([0-9a-f]\{7,40\}\)\.invalid/\{0,1\}$#\1#p' | head -n 1)
case "${URL_MAP:-}" in
*git-ref.invalid*)
  # Never fall back to main when the workflow asked for a commit: the result would
  # be reported against a PR it never tested.
  [ -n "$REF" ] || { echo "cannot parse the commit under test from URL_MAP: ${URL_MAP}" >&2; exit 1; } ;;
*) REF=main ;; # started from the 2027 dashboard without a mapping
esac

t "clone daytona/clients at ${REF}"
clone() {
  rm -rf /opt/clients
  git clone --quiet --filter=blob:none https://github.com/daytona/clients.git /opt/clients &&
    git -C /opt/clients fetch --quiet origin "$REF" &&
    git -C /opt/clients checkout --quiet --detach FETCH_HEAD
}
retry clone
cd /opt/clients
echo "build under test: $(git log --oneline -1)"

# Install the Go version go.work asks for. GOTOOLCHAIN=local makes the check report
# the installed Go instead of auto-switching (and downloading) inside /opt/clients.
# Only Go/CLI setups call this, so other languages don't depend on go.work.
install_go() {
  GO_VERSION=$(awk '/^go /{print $2; exit}' /opt/clients/go.work)
  if [ "$(GOTOOLCHAIN=local go env GOVERSION 2>/dev/null)" != "go${GO_VERSION}" ]; then
    t "install go ${GO_VERSION}"
    rm -rf /usr/local/go
    curl -fsSL --retry 3 "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" | tar -C /usr/local -xz
    ln -sf /usr/local/go/bin/go /usr/local/bin/go
  fi
  go version
}
