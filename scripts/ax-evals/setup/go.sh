# Copyright Daytona Platforms Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Prepare a Go module at /app/goeval that requires the Go SDK
# (go.daytona.com/sdk/v2) from the checked-out source through a replace
# directive, with its dependencies already downloaded.
install_go /opt/clients/sdk-go/go.mod
GO_MINIMUM=$(awk '/^go /{print $2; exit}' /opt/clients/sdk-go/go.mod)
t "download sdk-go modules"
cd /opt/clients/sdk-go
retry go mod download
$SUDO mkdir -p /app/goeval
$SUDO chown "$(id -u):$(id -g)" /app/goeval
cd /app/goeval
cat >go.mod <<EOF
module goeval

go ${GO_MINIMUM}

require go.daytona.com/sdk/v2 v2.0.0

replace go.daytona.com/sdk/v2 => /opt/clients/sdk-go
EOF
# Resolve go.sum and warm the build cache with a throwaway importer, then
# leave the module empty for the agent's code.
cat >warm.go <<'EOF'
package main

import "go.daytona.com/sdk/v2"

func main() { _, _ = daytona.New() }
EOF
t "resolve and build"
retry go mod tidy
go build ./...
rm warm.go
go list -m go.daytona.com/sdk/v2
t done
