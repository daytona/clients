// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestSandboxList_rejectsNoncanonicalCursorRegion(t *testing.T) {
	// Given: the catalog's canonical id is region-b, not REGION-B.
	// When
	stdout, _, err := runCLI(t, []string{"sandbox", "list", "--cursor", "REGION-B:page-two"}, []expectedRequest{{method: http.MethodGet, path: "/regions", status: 200, response: regionsFixture()}})
	// Then: do not silently restart at page one.
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
	require.ErrorContains(t, err, "--cursor")
	require.Empty(t, stdout)
}

func TestSandboxList_resumesExactCanonicalCursorRegion(t *testing.T) {
	// Given: the cursor names the exact canonical region key.
	// When
	stdout, _, err := runCLI(t, []string{"sandbox", "list", "--cursor", "region-b:page-two"}, []expectedRequest{
		{method: http.MethodGet, path: "/regions", status: 200, response: regionsFixture()},
		{method: http.MethodGet, path: "/sandboxes", query: "limit=100&cursor=page-two", status: 200, response: `{"sandboxes":[],"next_cursor":null}`},
	})
	// Then: fetch the saved page, not page one.
	require.NoError(t, err)
	require.Empty(t, requireJSONStdout(t, stdout)["items"])
}
