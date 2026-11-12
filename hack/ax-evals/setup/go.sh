# Prepare /app/goeval, a Go module whose daytona dependencies are replaced with
# the checked-out source.
install_go
t "prepare /app/goeval"
mkdir -p /app/goeval
cd /app/goeval
cat >go.mod <<GOMOD
module goeval

go ${GO_VERSION}

require github.com/daytona/clients/sdk-go v0.0.0

replace github.com/daytona/clients/sdk-go => /opt/clients/sdk-go
replace github.com/daytona/clients/api-client-go => /opt/clients/api-client-go
replace github.com/daytona/clients/toolbox-api-client-go => /opt/clients/toolbox-api-client-go
replace github.com/daytona/clients/analytics-api-client-go => /opt/clients/analytics-api-client-go
GOMOD
cat >warm.go <<'GO'
package main

import _ "github.com/daytona/clients/sdk-go/pkg/daytona"

func main() {}
GO
retry go mod tidy
go build ./...
rm warm.go
t done
