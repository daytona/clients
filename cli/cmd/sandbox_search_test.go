// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestSandboxSearch_routesFilters(t *testing.T) {
	stdout, _, err := runCLI(t, []string{
		"sandbox", "search", "python",
		"--status", "running",
		"--class", "linux",
		"--region", "region-b",
		"--limit", "25",
	}, []expectedRequest{{
		method:   http.MethodGet,
		path:     "/sandboxes/search",
		status:   200,
		query:    "q=python&status=running&class=linux&region_ids=region-b&limit=25",
		response: fixtureJSON(map[string]any{"next_cursor": "c2", "total": 1, "total_relation": "eq", "sandboxes": []any{sandboxSearchItemFixture("7fJk2mQpXv9dLc3RtY-region-b", "c1", "box", "region-b", "2026-09-13T00:00:00Z", "2026-09-13T00:00:00Z")}}),
	}})
	require.NoError(t, err)
	items := requireJSONStdout(t, stdout)["items"].([]any)
	require.Equal(t, "7fJk2mQpXv9dLc3RtY-region-b", items[0].(map[string]any)["id"])
}

func TestSandboxSearch_routesHalfOpenTimeFilters(t *testing.T) {
	_, _, err := runCLI(t, []string{
		"sandbox", "search",
		"--created-start-at", "2026-09-01T00:00:00Z",
		"--created-end-at", "2026-10-01T00:00:00Z",
		"--updated-start-at", "2026-09-02T00:00:00Z",
		"--updated-end-at", "2026-10-02T00:00:00Z",
		"--last-activity-start-at", "2026-09-03T00:00:00Z",
		"--last-activity-end-at", "2026-10-03T00:00:00Z",
	}, []expectedRequest{{
		method:   http.MethodGet,
		path:     "/sandboxes/search",
		status:   http.StatusOK,
		query:    "limit=100&created_start_at=2026-09-01T00%3A00%3A00Z&created_end_at=2026-10-01T00%3A00%3A00Z&updated_start_at=2026-09-02T00%3A00%3A00Z&updated_end_at=2026-10-02T00%3A00%3A00Z&last_activity_start_at=2026-09-03T00%3A00%3A00Z&last_activity_end_at=2026-10-03T00%3A00%3A00Z",
		response: `{"total":0,"total_relation":"eq","sandboxes":[]}`,
	}})
	require.NoError(t, err)
}

func TestSandboxSearch_preservesFractionalSecondsInTimeFilters(t *testing.T) {
	_, _, err := runCLI(t, []string{
		"sandbox", "search",
		"--created-start-at", "2026-09-01T00:00:00.123456789Z",
	}, []expectedRequest{{
		method:   http.MethodGet,
		path:     "/sandboxes/search",
		status:   http.StatusOK,
		query:    "limit=100&created_start_at=2026-09-01T00:00:00.123456789Z",
		response: `{"total":0,"total_relation":"eq","sandboxes":[]}`,
	}})
	require.NoError(t, err)
}

func TestSandboxSearch_allFollowsCursor(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "search", "box", "--all"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/search", status: 200, query: "q=box&limit=100", response: fixtureJSON(map[string]any{"next_cursor": "c2", "total": 2, "total_relation": "eq", "sandboxes": []any{sandboxSearchItemFixture("box-first", "c1", "box-a", "region-b", "2026-09-13T00:00:00Z", "2026-09-13T00:00:00Z")}})},
		{method: http.MethodGet, path: "/sandboxes/search", status: 200, query: "q=box&limit=100&cursor=c2", response: fixtureJSON(map[string]any{"total": 2, "total_relation": "eq", "sandboxes": []any{sandboxSearchItemFixture("box-second", "c2", "box-b", "region-c", "2026-09-13T00:00:01Z", "2026-09-13T00:00:01Z")}})},
	})
	require.NoError(t, err)
	require.Len(t, requireJSONStdout(t, stdout)["items"].([]any), 2)
}

func TestSandboxSearch_includesExplicitZeroFilters(t *testing.T) {
	stdout, _, err := runCLI(t, []string{
		"sandbox", "search", "python",
		"--max-cores", "0",
		"--max-memory", "0",
		"--max-disk", "0",
	}, []expectedRequest{{
		method:   http.MethodGet,
		path:     "/sandboxes/search",
		status:   200,
		query:    "q=python&limit=100&max_cores=0&max_memory_mib=0&max_disk_mib=0",
		response: `{"total":0,"total_relation":"eq","sandboxes":[]}`,
	}})
	require.NoError(t, err)
	require.Equal(t, float64(0), requireJSONStdout(t, stdout)["total"])
}

func TestSandboxSearch_sendsMemoryAndDiskVocabulary(t *testing.T) {
	_, _, err := runCLI(t, []string{
		"sandbox", "search",
		"--min-memory", "512",
		"--max-memory", "4096",
		"--min-disk", "1024",
		"--max-disk", "8192",
		"--sort", "disk_mib",
	}, []expectedRequest{{
		method:   http.MethodGet,
		path:     "/sandboxes/search",
		status:   200,
		query:    "limit=100&min_memory_mib=512&max_memory_mib=4096&min_disk_mib=1024&max_disk_mib=8192&sort=disk_mib",
		response: `{"total":0,"total_relation":"eq","sandboxes":[]}`,
	}})
	require.NoError(t, err)
}

func TestSandboxSearch_includesTotalsInStructuredOutput(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "search", "python"}, []expectedRequest{{
		method:   http.MethodGet,
		path:     "/sandboxes/search",
		status:   200,
		query:    "q=python&limit=100",
		response: `{"total":12345,"total_relation":"gte","sandboxes":[]}`,
	}})
	require.NoError(t, err)
	out := requireJSONStdout(t, stdout)
	require.Equal(t, float64(12345), out["total"])
	require.Equal(t, "gte", out["total_relation"])

	yamlStdout, _, yamlErr := runCLIWithOutput(t, "yaml", []string{"sandbox", "search", "python"}, []expectedRequest{{
		method:   http.MethodGet,
		path:     "/sandboxes/search",
		status:   200,
		query:    "q=python&limit=100",
		response: `{"total":12345,"total_relation":"gte","sandboxes":[]}`,
	}})
	require.NoError(t, yamlErr)
	require.Contains(t, yamlStdout, "total: 12345")
	require.Contains(t, yamlStdout, "total_relation: gte")
}

func TestSandboxSearch_regionFlagBeforeTheCommandName(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"--region", "region-b", "sandbox", "search", "python"}, []expectedRequest{{
		method:   http.MethodGet,
		path:     "/sandboxes/search",
		status:   200,
		query:    "q=python&limit=100&region_ids=region-b",
		response: `{"total":0,"total_relation":"eq","sandboxes":[]}`,
	}})
	require.NoError(t, err)
	require.Equal(t, float64(0), requireJSONStdout(t, stdout)["total"])
}

// The query is optional in the contract: filters alone select a set.
func TestSandboxSearch_filterOnlyWithoutQuery(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "search", "--status", "running"}, []expectedRequest{
		{method: http.MethodGet, path: "/sandboxes/search", status: 200, query: "limit=100&status=running", response: `{"sandboxes":[],"next_cursor":null,"total":0,"total_relation":"eq"}`},
	})
	require.NoError(t, err)
	require.NotEmpty(t, stdout)
}

// Search answers from one API: its output has no `incomplete` or `errors`.
func TestSandboxSearch_outputShape(t *testing.T) {
	stdout, _, err := runCLI(t, []string{"sandbox", "search", "python"}, []expectedRequest{{
		method:   http.MethodGet,
		path:     "/sandboxes/search",
		status:   200,
		query:    "q=python&limit=100",
		response: `{"total":0,"total_relation":"eq","sandboxes":[],"next_cursor":"c2"}`,
	}})
	require.NoError(t, err)
	out := requireJSONStdout(t, stdout)
	keys := make([]string, 0, len(out))
	for k := range out {
		keys = append(keys, k)
	}
	require.ElementsMatch(t, []string{"items", "next_cursor", "total", "total_relation"}, keys)
}
