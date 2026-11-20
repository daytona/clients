// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

var routeParameter = regexp.MustCompile(`\{[^/{}]+\}`)

// Keep this independent of TypeScript at runtime: published Go module archives
// do not include either sibling. The optional parity test checks the copied list.
var toolboxRouteAllowlist = map[string]string{
	"GET /info":             "No public SDK method exposes this metadata endpoint.",
	"GET /sessions/{param}": "Session handles expose operations without a separate metadata lookup.",
	"GET /version":          "No public SDK method exposes this version endpoint.",
}

func TestSDKRoutesMatchContracts(t *testing.T) {
	// Given every request description in the public package and both contracts.
	operations := readRouteContracts(t)
	scan := scanSDKRoutes(t)
	require.Empty(t, scan.errors, "route collector must account for every request and forwarding helper")

	// When binding each route to its transport's contract operation.
	failures := routeContractFailures(scan.routes, operations)

	// Then every route has exactly one operation and toolbox coverage is complete.
	require.Empty(t, failures)
	t.Logf("checked %d route uses against %d contract operations", len(scan.routes), len(operations))
}

func TestSDKRouteForwardingInventoryIsCurrent(t *testing.T) {
	// Given explicit descriptions only for paths whose values flow across helpers.
	scan := scanSDKRoutes(t)

	// When comparing the inventory with all AST sites encountered.
	var stale []string
	for key := range routeForwarders {
		if !scan.seen[key] {
			stale = append(stale, key)
		}
	}
	for key := range routeAnchors {
		if !scan.seen[key] {
			stale = append(stale, key)
		}
	}

	// Then deleted or changed builders cannot leave claimed coverage behind.
	slices.Sort(stale)
	require.Empty(t, stale)
}

func TestSDKToolboxRouteAllowlistMatchesTypeScript(t *testing.T) {
	// Given the optional sibling TypeScript source.
	source := readRouteSibling(t, "../sdk-typescript/tests/unit.test.ts")
	block := regexp.MustCompile(`(?s)const SDK_TOOLBOX_ROUTE_ALLOWLIST = \{(.*?)\n\}`).FindSubmatch(source)
	require.Len(t, block, 2, "cannot find TypeScript route allow-list")
	entries := regexp.MustCompile(`'(GET [^']+)':`).FindAllSubmatch(block[1], -1)
	expected := make([]string, 0, len(entries))
	for _, entry := range entries {
		expected = append(expected, routeParameter.ReplaceAllString(string(entry[1]), "{param}"))
	}
	actual := make([]string, 0, len(toolboxRouteAllowlist))
	for key := range toolboxRouteAllowlist {
		actual = append(actual, key)
	}

	// When comparing operation keys, independent of each language's reason text.
	slices.Sort(expected)
	slices.Sort(actual)

	// Then both SDKs intentionally omit the same toolbox operations.
	require.Equal(t, expected, actual)
}

func scanSDKRoutes(t *testing.T) sdkRouteScan {
	t.Helper()
	files, err := filepath.Glob("*.go")
	require.NoError(t, err)
	scan := sdkRouteScan{seen: make(map[string]bool)}
	for _, filename := range files {
		if strings.HasSuffix(filename, "_test.go") {
			continue
		}
		source, readErr := os.ReadFile(filename)
		require.NoError(t, readErr)
		scan.source(filename, source)
	}
	return scan
}

func readRouteSibling(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		t.Skip("route drift requires optional repository sibling: " + path)
	}
	require.NoError(t, err)
	return data
}

func readRouteContracts(t *testing.T) []sdkRoute {
	t.Helper()
	var operations []sdkRoute
	for _, contract := range []string{"toolbox", "api"} {
		data := readRouteSibling(t, "../contract/"+contract+"-openapi.json")
		var document struct {
			Paths map[string]map[string]json.RawMessage `json:"paths"`
		}
		require.NoError(t, json.Unmarshal(data, &document))
		require.NotEmpty(t, document.Paths)
		for path, methods := range document.Paths {
			for method := range methods {
				switch method {
				case "get", "post", "put", "patch", "delete", "head", "options", "trace":
					operations = append(operations, sdkRoute{
						contract, strings.ToUpper(method), routeParameter.ReplaceAllString(path, "{param}"),
					})
				}
			}
		}
	}
	return operations
}

func routeContractFailures(routes, operations []sdkRoute) []string {
	var failures []string
	called := make(map[sdkRoute]bool)
	for _, route := range routes {
		count := 0
		for _, operation := range operations {
			if operation == route {
				count++
			}
		}
		if count != 1 {
			failures = append(failures, route.contract+" "+route.method+" "+route.path+" must match exactly one operation")
		}
		called[route] = true
	}
	for _, operation := range operations {
		if operation.contract != "toolbox" {
			continue
		}
		key := operation.method + " " + operation.path
		_, allowed := toolboxRouteAllowlist[key]
		switch {
		case called[operation] && allowed:
			failures = append(failures, "stale toolbox allow-list: "+key)
		case !called[operation] && !allowed:
			failures = append(failures, "unused toolbox operation: "+key)
		}
	}
	for key := range toolboxRouteAllowlist {
		if !slices.ContainsFunc(operations, func(operation sdkRoute) bool {
			return operation.contract == "toolbox" && operation.method+" "+operation.path == key
		}) {
			failures = append(failures, "missing allow-listed toolbox operation: "+key)
		}
	}
	slices.Sort(failures)
	return failures
}
