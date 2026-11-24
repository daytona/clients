// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"slices"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSDKRouteScannerRejectsPathAndMethodMutations(t *testing.T) {
	tests := []struct {
		name   string
		source string
	}{
		{"path typo", `toolboxRequest{Path: "/filez"}`},
		{"wrong method", `toolboxRequest{Method: http.MethodPatch, Path: "/files"}`},
		{"wrong transport", `apiRequest{Path: "/files"}`},
		{"alias on wrong transport", `client.api().do(ctx, toolboxRequest{Path: "/files"})`},
		{"new path", `toolboxRequest{Path: "/new-endpoint"}`},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given a known operation and a mutated production-style request.
			scan := sdkRouteScan{seen: make(map[string]bool)}
			scan.source("fixture.go", []byte("package daytona; var request = "+test.source))
			require.Empty(t, scan.errors)
			operations := []sdkRoute{{"toolbox", "GET", "/files"}}

			// When binding the AST-derived route to the contract.
			failures := routeContractFailures(scan.routes, operations)

			// Then neither a typo nor the wrong method/transport can bind.
			require.Contains(t, strings.Join(failures, "\n"), "must match exactly one operation")
		})
	}
}

func TestSDKRouteScannerNormalizesOnlyPathIDs(t *testing.T) {
	// Given IDs concatenated into paths, including a numeric port and a local variable.
	source := `package daytona
	func routes() {
		request := toolboxRequest{Path: "/processes/" + segment + "/logs"}
		port := toolboxRequest{Path: "/port/" + strconv.Itoa(port) + "/in-use"}
		_ = request; _ = port
	}`
	scan := sdkRouteScan{seen: make(map[string]bool)}

	// When inventorying the source.
	scan.source("fixture.go", []byte(source))

	// Then literal segments survive while encoded identifiers become {param}.
	require.Empty(t, scan.errors)
	require.Equal(t, []sdkRoute{
		{"toolbox", "GET", "/processes/{param}/logs"},
		{"toolbox", "GET", "/port/{param}/in-use"},
	}, scan.routes)
}

func TestSDKRouteScannerRejectsUninventoriedBuilders(t *testing.T) {
	tests := []struct {
		name     string
		filename string
		body     string
	}{
		{"unknown variable", "fixture.go", `request := toolboxRequest{Path: route}`},
		{"unknown identifier segment", "fixture.go", `request := toolboxRequest{Path: "/processes/" + route}`},
		{"path assignment typo", "computer_use_screen.go", `spec.Path += "/regoin"`},
		{"method assignment typo", "computer_use_process.go", `spec.Method = http.MethodGet`},
		{"prepared path typo", "sandbox_create.go", `prepared.path, prepared.class = "/sandbozes", SandboxClassLinux`},
		{"helper suffix typo", "image_list.go", `read(r.path + "/searhc")`},
		{"helper ID prefix typo", "ports.go", `return "/sandbozes/" + segment + "/ports", nil`},
		{"addon operation typo", "computer_use_process.go", `s.read(ctx, name, "stats")`},
		{"unclassified Path", "fixture.go", `request := newRequest{Path: "/files"}`},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given a mutation in a forwarding seam, without editing real source.
			function := "routes"
			if test.name == "helper ID prefix typo" {
				function = "portsPath"
			}
			source := "package daytona; func " + function + "() { " + test.body + " }"
			scan := sdkRouteScan{seen: make(map[string]bool)}

			// When scanning the mutated AST fixture.
			scan.source(test.filename, []byte(source))

			// Then the new builder must be explicitly inventoried, never dropped.
			require.NotEmpty(t, scan.errors)
		})
	}
}

func TestSDKRouteContractGuardRejectsCoverageDrift(t *testing.T) {
	tests := []struct {
		name       string
		routes     []sdkRoute
		operations []sdkRoute
		message    string
	}{
		{
			name:       "new unused toolbox operation",
			operations: []sdkRoute{{"toolbox", "POST", "/new-operation"}},
			message:    "unused toolbox operation: POST /new-operation",
		},
		{
			name:       "stale allow-list entry",
			routes:     []sdkRoute{{"toolbox", "GET", "/info"}},
			operations: []sdkRoute{{"toolbox", "GET", "/info"}},
			message:    "stale toolbox allow-list: GET /info",
		},
		{
			name:   "ambiguous normalized operations",
			routes: []sdkRoute{{"api", "GET", "/items/{param}"}},
			operations: []sdkRoute{
				{"api", "GET", "/items/{param}"}, {"api", "GET", "/items/{param}"},
			},
			message: "api GET /items/{param} must match exactly one operation",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given a new operation, stale omission, or ambiguous contract.
			operations := slices.Clone(test.operations)

			// When checking call coverage and exact operation binding.
			failures := routeContractFailures(test.routes, operations)

			// Then the specific regression is reported.
			require.Contains(t, failures, test.message)
		})
	}
}
