# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Build the daytona CLI from the checked-out source into /opt/daytona-cli/bin. The
# eval image may mount another file at /usr/local/bin/daytona, so the build under
# test gets its own directory, linked from the user's bin directories that
# ~/.profile puts first on PATH; the prompt also names the absolute path.
install_go
t "build cli"
cd /opt/clients/cli
retry go build -o /tmp/daytona .
$SUDO mkdir -p /opt/daytona-cli/bin
$SUDO install -m 0755 /tmp/daytona /opt/daytona-cli/bin/daytona
mkdir -p "$HOME/.local/bin" "$HOME/bin"
ln -sf /opt/daytona-cli/bin/daytona "$HOME/.local/bin/daytona"
ln -sf /opt/daytona-cli/bin/daytona "$HOME/bin/daytona"
/opt/daytona-cli/bin/daytona --version
resolved=$(bash -lc 'command -v daytona' || true)
echo "login shell resolves daytona to: ${resolved:-nothing}"
[ "$(readlink -f "${resolved:-/nonexistent}")" = /opt/daytona-cli/bin/daytona ] ||
  echo "warning: a login shell does not resolve daytona to the build under test; the prompt names /opt/daytona-cli/bin/daytona" >&2
t done
