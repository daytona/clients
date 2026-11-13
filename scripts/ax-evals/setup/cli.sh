# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Build the daytona CLI from the checked-out source onto PATH.
install_go
t "build cli"
cd /opt/clients/cli
retry go build -o /tmp/daytona .
$SUDO install -m 0755 /tmp/daytona /usr/local/bin/daytona
daytona --version
t done
