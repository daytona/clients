# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Shared preamble for every AX eval setup script. sync.py prepends it to
# setup/<surface>.sh before uploading the result as the prompt's setupCommand.
#
# Firegoat does not substitute template variables into setupCommand, but it does
# expose the run's urlMap to setup as URL_MAP ("host=upstream"). The workflow
# encodes the commit under test as {"git-ref.invalid": "https://<sha>.invalid"};
# runs started from the Firegoat dashboard without that mapping test main.
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
# Installs go to system paths; setup may run as root or as a sudo-capable user.
SUDO=""
[ "$(id -u)" = 0 ] || SUDO="sudo"

REF=$(printf '%s\n' "${URL_MAP:-}" | tr ', ;' '\n\n\n' | tr 'A-F' 'a-f' |
  sed -n 's#^git-ref\.invalid=https\{0,1\}://\([0-9a-f]\{40\}\)\.invalid/\{0,1\}$#\1#p' | head -n 1)
case "${URL_MAP:-}" in
*git-ref.invalid*)
  # Never fall back to main when the workflow asked for a commit: the result would
  # be reported against a PR it never tested.
  [ -n "$REF" ] || { echo "cannot parse the commit under test from URL_MAP: ${URL_MAP}" >&2; exit 1; } ;;
*) REF=main ;; # started from the Firegoat dashboard without a mapping
esac

t "clone daytona/clients at ${REF}"
clone() {
  $SUDO rm -rf /opt/clients
  $SUDO mkdir -p /opt/clients
  $SUDO chown "$(id -u):$(id -g)" /opt/clients
  git clone --quiet --filter=blob:none https://github.com/daytona/clients.git /opt/clients &&
    git -C /opt/clients fetch --quiet origin "$REF" &&
    git -C /opt/clients checkout --quiet --detach FETCH_HEAD
}
retry clone
cd /opt/clients
echo "build under test: $(git log --oneline -1)"

# Install the Go version cli/go.mod asks for. GOTOOLCHAIN=local makes the check
# report the installed Go instead of auto-switching (and downloading).
install_go() {
  GO_VERSION=$(awk '/^go /{print $2; exit}' /opt/clients/cli/go.mod)
  if [ "$(GOTOOLCHAIN=local go env GOVERSION 2>/dev/null)" != "go${GO_VERSION}" ]; then
    t "install go ${GO_VERSION}"
    $SUDO rm -rf /usr/local/go
    curl -fsSL --retry 3 "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" | $SUDO tar -C /usr/local -xz
    $SUDO ln -sf /usr/local/go/bin/go /usr/local/bin/go
  fi
  go version
}

# Install the newest Node.js of the needed major when the image's Node does not satisfy the TypeScript
# SDK's engines field (a ">=X.Y.Z" floor), then fail unless the node on PATH does.
node_satisfies() {
  node -e '
    const floor = process.argv[1].replace(/^>=\s*/, "").split(".").map(Number)
    const have = process.versions.node.split(".").map(Number)
    for (let i = 0; i < 3; i++) if ((have[i] || 0) !== (floor[i] || 0)) process.exit((have[i] || 0) > (floor[i] || 0) ? 0 : 1)
  ' "$1" 2>/dev/null
}
install_node() {
  local need major version
  need=$(python3 -c 'import json; print(json.load(open("/opt/clients/sdk-typescript/package.json"))["engines"]["node"])')
  case "$need" in ">="[0-9]*) ;; *) echo "unsupported engines.node '${need}': expected >=X.Y.Z" >&2; return 1 ;; esac
  if ! node_satisfies "$need"; then
    major=$(printf '%s' "${need#>=}" | tr -d ' ' | cut -d. -f1)
    version=$(curl -fsSL --retry 3 https://nodejs.org/dist/index.json |
      python3 -c 'import json,re,sys; m=sys.argv[1]; print(next(r["version"] for r in json.load(sys.stdin) if re.fullmatch(r"v"+m+r"\.\d+\.\d+", r["version"])))' "$major")
    t "install node ${version} (image has $(node -v 2>/dev/null || echo none); SDK needs ${need})"
    # Its own prefix: unpacking over the image's /usr/local mixes two npm installs.
    $SUDO rm -rf /opt/node
    $SUDO mkdir -p /opt/node
    curl -fsSL --retry 3 "https://nodejs.org/dist/${version}/node-${version}-linux-x64.tar.xz" |
      $SUDO tar -C /opt/node --strip-components=1 -xJ
    for bin in node npm npx corepack; do $SUDO ln -sf "/opt/node/bin/${bin}" "/usr/local/bin/${bin}"; done
    hash -r
  fi
  node_satisfies "$need" || { echo "node $(node -v) on PATH does not satisfy ${need}" >&2; return 1; }
  node -v
}
