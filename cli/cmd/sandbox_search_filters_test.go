// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestSandboxSearch_mergesLabelsWhenCompatible(t *testing.T) {
	// Given
	args := []string{"sandbox", "search", "--labels", `{"team":"infra"}`, "--label", "team=infra", "--label", "name=worker"}
	// When
	_, _, err := runCLI(t, args, []expectedRequest{{
		method: http.MethodGet, path: "/sandboxes/search", status: 200, response: `{"total":0,"total_relation":"eq","sandboxes":[]}`,
		check: func(t *testing.T, r *http.Request, _ []byte) {
			var labels map[string]string
			require.NoError(t, json.Unmarshal([]byte(r.URL.Query().Get("labels")), &labels))
			require.Equal(t, map[string]string{"team": "infra", "name": "worker"}, labels)
		},
	}})
	// Then
	require.NoError(t, err)
}

func TestSandboxSearch_refusesLabelsWhenMalformedOrConflicting(t *testing.T) {
	for _, flags := range [][]string{
		{"--labels", `{"team":"infra"}`, "--label", "team=app"},
		{"--label", "bad"}, {"--label", "=value"},
		{"--labels", `{"team":1}`}, {"--labels", `{"team":null}`}, {"--labels", `[]`}, {"--labels", `null`},
	} {
		t.Run(strings.Join(flags, " "), func(t *testing.T) {
			// Given
			args := append([]string{"sandbox", "search"}, flags...)
			// When
			_, _, err := runCLI(t, args, nil)
			// Then
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		})
	}
}

func TestSandboxSearch_refusesRegionWhenUnknown(t *testing.T) {
	// Given
	// When
	_, _, err := runCLI(t, []string{"sandbox", "search", "--region", "nowhere"}, []expectedRequest{{method: http.MethodGet, path: "/regions", status: 200, response: regionsFixture()}})
	// Then
	require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
}

func TestSandboxSearch_expandsRegionsWhenParentSelected(t *testing.T) {
	// Given
	// When
	_, _, err := runCLI(t, []string{"sandbox", "search", "--region", "EARTH"}, []expectedRequest{
		{method: http.MethodGet, path: "/regions", status: 200, response: regionsFixture(`{"id":"earth","name":"Earth","available":true,"parent_region_ids":[]}`)},
		{method: http.MethodGet, path: "/sandboxes/search", status: 200, query: "limit=100&region_ids=earth,region-b", response: `{"total":0,"total_relation":"eq","sandboxes":[]}`},
	})
	// Then
	require.NoError(t, err)
}
