// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"math"
	"mime"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type expectedRequest struct {
	method      string
	path        string
	query       string
	bodyJSON    string
	contentType string
	status      int
	response    string
	check       func(*testing.T, *http.Request, []byte)
}

type runCLIOptions struct {
	output         string
	contextTimeout time.Duration
}

func runCLI(t *testing.T, args []string, expected []expectedRequest) (string, string, error) {
	t.Helper()
	return runCLIWithOptions(t, runCLIOptions{output: "json"}, args, expected)
}

func runCLIWithOutput(t *testing.T, output string, args []string, expected []expectedRequest) (string, string, error) {
	t.Helper()
	return runCLIWithOptions(t, runCLIOptions{output: output}, args, expected)
}

func runCLIWithOptions(t *testing.T, opts runCLIOptions, args []string, expected []expectedRequest) (string, string, error) {
	t.Helper()
	var index int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !assert.Less(t, index, len(expected), "unexpected request %s %s", r.Method, r.URL.String()) {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		exp := expected[index]
		index++
		assert.Equal(t, exp.method, r.Method)
		assert.Equal(t, exp.path, r.URL.Path)
		assert.Equal(t, "Bearer k", r.Header.Get("Authorization"))
		assert.Equal(t, "cli", r.Header.Get("X-Daytona-Source"))
		if exp.contentType != "" {
			assert.Contains(t, r.Header.Get("Content-Type"), exp.contentType)
		}
		if exp.query != "" {
			requireQueryEqual(t, exp.query, r.URL.RawQuery)
		}
		body, err := io.ReadAll(r.Body)
		assert.NoError(t, err)
		if exp.bodyJSON != "" {
			assert.JSONEq(t, exp.bodyJSON, string(body))
		} else if exp.check == nil {
			assert.Empty(t, strings.TrimSpace(string(body)))
		}
		if exp.check != nil {
			exp.check(t, r, body)
		}
		// The same contract harness the fakeAPI runs: request AND fixture
		// response validated against the OpenAPI documents.
		validateRequestAgainstContract(t, r, body)
		if exp.response != "" {
			if strings.HasPrefix(exp.response, "text/plain:") {
				w.Header().Set("Content-Type", "text/plain")
				w.WriteHeader(exp.status)
				_, _ = io.WriteString(w, strings.TrimPrefix(exp.response, "text/plain:"))
				return
			}
			w.Header().Set("Content-Type", "application/json")
			validateAgainstContractRequest(t, r, exp.status, []byte(exp.response), "application/json")
		}
		w.WriteHeader(exp.status)
		_, _ = io.WriteString(w, exp.response)
	}))
	defer server.Close()

	t.Setenv("DAYTONA_CONFIG_DIR", t.TempDir())
	for _, key := range []string{"DAYTONA_API_URL", "DAYTONA_V2_API_URL", "DAYTONA_API_KEY", "DAYTONA_PROFILE", "DAYTONA_ORG", "DAYTONA_PROJECT", "DAYTONA_REGION", "DAYTONA_OIDC_CLIENT_ID", "DAYTONA_YES"} {
		t.Setenv(key, "")
	}
	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	output := opts.output
	if output == "" {
		output = "json"
	}
	root.SetArgs(append([]string{"--api-url", server.URL, "--api-key", "k", "-o", output}, args...))
	contextTimeout := opts.contextTimeout
	if contextTimeout == 0 {
		contextTimeout = 2 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), contextTimeout)
	defer cancel()
	err := root.ExecuteContext(ctx)
	if err == nil {
		require.Equal(t, len(expected), index)
	}
	return stdout.String(), stderr.String(), err
}

func requireQueryEqual(t *testing.T, want, got string) {
	t.Helper()
	wantQ, err := url.ParseQuery(want)
	require.NoError(t, err)
	gotQ, err := url.ParseQuery(got)
	require.NoError(t, err)
	require.Equal(t, wantQ, gotQ)
}

func requireJSONStdout(t *testing.T, stdout string) map[string]any {
	t.Helper()
	var out map[string]any
	require.NoError(t, json.Unmarshal([]byte(stdout), &out))
	return out
}

func toolboxSandboxDetailFixture(id, class, status string) string {
	return strings.ReplaceAll(sandboxDetailFixture(id, class, status), "https://toolbox.example", "__SERVER__/test-tools")
}

func sandboxDetailFixture(id, class, status string) string {
	return `{"class":"` + class + `","cpu":1,"id":"` + id + `","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"box","port_map":[],"region_id":"region-b","disk_mib":1024,"spot":false,"status":"` + status + `","toolbox_proxy_url":"https://toolbox.example","cores_max":1}`
}

func sandboxPageFixture(id, cursor string) string {
	return `{"next_cursor":"n1","sandboxes":[{"class":"linux","cpu":1,"created_at":"2026-09-13T00:00:00Z","cursor":"` + cursor + `","id":"` + id + `","image":{"id":"img_py312","name":"python:3.12"},"labels":{},"lifecycle_policy":{},"memory_mib":512,"name":"box","port_map":[],"region_id":"region-b","disk_mib":1024,"spot":false,"status":"running","cores_max":1}]}`
}

func sandboxPageItemFixture(id, cursor, name, region, createdAt string) map[string]any {
	return map[string]any{
		"id":               id,
		"name":             name,
		"class":            "linux",
		"status":           "running",
		"region_id":        region,
		"image":            map[string]any{"id": "img_py312", "name": "python:3.12"},
		"created_at":       createdAt,
		"cursor":           cursor,
		"cpu":              1,
		"cores_max":        1,
		"memory_mib":       512,
		"disk_mib":         1024,
		"port_map":         []any{},
		"labels":           map[string]any{},
		"lifecycle_policy": map[string]any{},
		"spot":             false,
	}
}

func sandboxSearchItemFixture(id, cursor, name, region, createdAt, updatedAt string) map[string]any {
	item := sandboxPageItemFixture(id, cursor, name, region, createdAt)
	item["updated_at"] = updatedAt
	item["toolbox_proxy_url"] = "https://proxy." + region + ".example/test-tools"
	return item
}

func imageFixture(id, status string) string {
	return `{"anonymous":false,"region_ids":["region-b"],"class":"linux","created_at":"2026-09-13T00:00:00Z","updated_at":"2026-09-13T00:00:00Z","format_outdated":false,"format_version":2,"platform":false,"default":false,"deprecated":false,"id":"` + id + `","name":"img","origin":"registry","region_id":"region-b","retention":"permanent","source":{"type":"registry","ref":"python:3.12"},"status":"` + status + `"}`
}

func imagePageFixture(id string) string {
	return `{"next_cursor":"n1","images":[{"anonymous":false,"region_ids":["region-b"],"class":"linux","created_at":"2026-09-13T00:00:00Z","updated_at":"2026-09-13T00:00:00Z","cursor":"c1","format_outdated":false,"format_version":2,"platform":false,"default":false,"deprecated":false,"id":"` + id + `","name":"img","origin":"registry","region_id":"region-b","retention":"permanent","source":{"type":"registry","ref":"python:3.12"},"status":"ready"}]}`
}

func snapshotFixture(id, kind, status string) string {
	return `{"region_ids":["region-b"],"class":"linux","created_at":"2026-09-13T00:00:00Z","updated_at":"2026-09-13T00:00:00Z","id":"` + id + `","kind":"` + kind + `","name":"snap","region_id":"region-b","status":"` + status + `"}`
}

func snapshotPageFixture(id string) string {
	return `{"next_cursor":"n1","snapshots":[{"region_ids":["region-b"],"class":"linux","created_at":"2026-09-13T00:00:00Z","updated_at":"2026-09-13T00:00:00Z","cursor":"c1","id":"` + id + `","kind":"full","name":"snap","region_id":"region-b","status":"ready"}]}`
}

func TestCatalogCommands_routeAndRenderJSON(t *testing.T) {
	tests := []struct {
		name string
		args []string
		exp  expectedRequest
	}{
		{name: "region list", args: []string{"region", "list"}, exp: expectedRequest{method: http.MethodGet, path: "/regions", status: 200, response: `{"regions":[{"id":"region-b","name":"Region B","available":true,"parent_region_ids":["earth"]}],"default_region_id":null}`}},
		{name: "cpu type list", args: []string{"cpu-type", "list"}, exp: expectedRequest{method: http.MethodGet, path: "/cpu-catalog", status: 200, response: `{"regions":[],"arches":[{"id":"amd64","available":false,"region_ids":[]}],"cpu_types":[{"id":"zen5","name":"Zen 5","cpu_arch":"amd64","tier":1,"available":false,"exact_region_ids":[],"fallback_region_ids":[]}]}`}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			stdout, _, err := runCLI(t, tt.args, []expectedRequest{tt.exp})
			require.NoError(t, err)
			require.NotEmpty(t, requireJSONStdout(t, stdout))
		})
	}
}

func cpuCatalogFixture() string {
	return `{"regions":[{"id":"region-b","default_arch":"amd64","default_type":"zen5"}],` +
		`"arches":[{"id":"amd64","available":true,"region_ids":["region-b"]},{"id":"arm64","available":false,"region_ids":[]}],` +
		`"cpu_types":[` +
		`{"id":"zen3","name":"AMD Zen 3","cpu_arch":"amd64","tier":1,"available":true,"exact_region_ids":[],"fallback_region_ids":["region-b"]},` +
		`{"id":"zen5","name":"AMD Zen 5","cpu_arch":"amd64","tier":3,"available":true,"exact_region_ids":["region-b"],"fallback_region_ids":[]},` +
		`{"id":"arm-basic","name":"ARM Basic","cpu_arch":"arm64","tier":2,"available":false,"exact_region_ids":[],"fallback_region_ids":[]}]}`
}

// regionsFixture is a GET /regions catalog. The default URL is used when
// api_base_url is absent; extra appends regions.
func regionsFixture(extra ...string) string {
	items := append([]string{`{"id":"region-b","name":"Region B","available":true,"parent_region_ids":["earth"]}`}, extra...)
	return `{"regions":[` + strings.Join(items, ",") + `],"default_region_id":null}`
}

func regionalAPIRow(id, apiBaseURL string) string {
	return `{"id":"` + id + `","name":"Example Region","available":true,"parent_region_ids":["earth"],"api_base_url":"` + apiBaseURL + `"}`
}

// `--region` resolves the slug through the `GET /regions` routing catalog
// (regions.BaseFor, like sandbox routing), asks that regional API's
// `GET /cpu-catalog` and adds the AVAILABLE column; without it the command
// lists every type from the default API's `GET /cpu-catalog`.
func TestCPUTypeList_regionUsesCatalog(t *testing.T) {
	// A region without api_base_url is served by the default API, so the
	// catalog request stays on the default base after the routing lookup.
	routing := expectedRequest{method: http.MethodGet, path: "/regions", status: 200, response: regionsFixture()}
	catalog := expectedRequest{method: http.MethodGet, path: "/cpu-catalog", query: "region_ids=region-b", status: 200, response: cpuCatalogFixture()}

	t.Run("table renders availability per region", func(t *testing.T) {
		stdout, _, err := runCLIWithOutput(t, "table", []string{"cpu-type", "list", "--region", "region-b"}, []expectedRequest{routing, catalog})
		require.NoError(t, err)
		lines := strings.Split(strings.TrimSpace(stdout), "\n")
		require.Len(t, lines, 4)
		require.Equal(t, []string{"ID", "NAME", "ARCH", "TIER", "AVAILABLE"}, strings.Fields(lines[0]))
		require.Equal(t, []string{"zen3", "AMD", "Zen", "3", "amd64", "1", "fallback"}, strings.Fields(lines[1]))
		require.Equal(t, []string{"zen5", "AMD", "Zen", "5", "amd64", "3", "exact"}, strings.Fields(lines[2]))
		require.Equal(t, []string{"arm-basic", "ARM", "Basic", "arm64", "2", "-"}, strings.Fields(lines[3]))
	})

	t.Run("json is the list envelope of the region's CPU types", func(t *testing.T) {
		stdout, _, err := runCLI(t, []string{"cpu-type", "list", "--region", "region-b"}, []expectedRequest{routing, catalog})
		require.NoError(t, err)
		var fixture struct {
			CPUTypes []json.RawMessage `json:"cpu_types"`
		}
		require.NoError(t, json.Unmarshal([]byte(cpuCatalogFixture()), &fixture))
		want, err := json.Marshal(map[string]any{"items": fixture.CPUTypes, "incomplete": false})
		require.NoError(t, err)
		require.JSONEq(t, string(want), stdout)
	})

	t.Run("empty catalog prints no results", func(t *testing.T) {
		empty := catalog
		empty.response = `{"regions":[{"id":"region-b","default_arch":"amd64"}],"arches":[{"id":"amd64","available":false,"region_ids":[]},{"id":"arm64","available":false,"region_ids":[]}],"cpu_types":[]}`
		stdout, _, err := runCLIWithOutput(t, "table", []string{"cpu-type", "list", "--region", "region-b"}, []expectedRequest{routing, empty})
		require.NoError(t, err)
		require.Equal(t, "No results.\n", stdout)
	})

	// The CLI normalizes region inputs before requests and comparisons; it must
	// send and compare the same slug or every row renders as `-`.
	t.Run("mixed-case and whitespace --region normalizes to the catalog slug", func(t *testing.T) {
		stdout, _, err := runCLIWithOutput(t, "table", []string{"cpu-type", "list", "--region", "  Region-B "}, []expectedRequest{routing, catalog})
		require.NoError(t, err)
		lines := strings.Split(strings.TrimSpace(stdout), "\n")
		require.Len(t, lines, 4)
		require.Equal(t, []string{"zen3", "AMD", "Zen", "3", "amd64", "1", "fallback"}, strings.Fields(lines[1]))
		require.Equal(t, []string{"zen5", "AMD", "Zen", "5", "amd64", "3", "exact"}, strings.Fields(lines[2]))
		require.Equal(t, []string{"arm-basic", "ARM", "Basic", "arm64", "2", "-"}, strings.Fields(lines[3]))
	})

	t.Run("comma-separated --region is a usage error before any request", func(t *testing.T) {
		for _, value := range []string{"region-b,region-c", "region-b,", ",", "   ", ""} {
			_, stderr, err := runCLIWithOutput(t, "table", []string{"cpu-type", "list", "--region", value}, nil)
			require.Error(t, err, value)
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err), value)
			if strings.TrimSpace(value) == "" {
				require.Contains(t, err.Error(), "--region is empty", value)
			} else {
				require.Contains(t, err.Error(), "--region takes one region slug", value)
			}
			require.Empty(t, stderr, value)
		}
	})

	t.Run("a 200 whose regions omit the slug is an error, not a table of dashes", func(t *testing.T) {
		other := catalog
		other.response = strings.Replace(cpuCatalogFixture(), `"regions":[{"id":"region-b",`, `"regions":[{"id":"region-c",`, 1)
		stdout, _, err := runCLIWithOutput(t, "table", []string{"cpu-type", "list", "--region", "region-b"}, []expectedRequest{routing, other})
		require.Error(t, err)
		require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
		require.Contains(t, err.Error(), "region-b")
		require.Contains(t, err.Error(), "region-c")
		require.Empty(t, stdout)

		none := catalog
		none.response = `{"regions":[],"arches":[{"id":"amd64","available":false,"region_ids":[]},{"id":"arm64","available":false,"region_ids":[]}],"cpu_types":[]}`
		stdout, _, err = runCLIWithOutput(t, "table", []string{"cpu-type", "list", "--region", "region-b"}, []expectedRequest{routing, none})
		require.Error(t, err)
		require.Contains(t, err.Error(), "no regions")
		require.Empty(t, stdout)
	})

	// A region served by another API carries its api_base_url in the region
	// catalog; the default API would answer 409 for it, so the catalog request
	// must go to that endpoint. The default API only ever sees GET /regions.
	t.Run("a region with another api_base_url is asked at that endpoint", func(t *testing.T) {
		// Handlers run on the server goroutine: only non-fatal assertions
		// there (a FailNow would kill the handler mid-response and hide the
		// real failure), and nothing shared with the test goroutine without
		// synchronization (the call counters are atomic; the query is asserted
		// in place rather than recorded).
		var regionalCalls, homeCalls atomic.Int32
		regional := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			regionalCalls.Add(1)
			if gotQ, err := url.ParseQuery(r.URL.RawQuery); assert.NoError(t, err) {
				assert.Equal(t, url.Values{"region_ids": {"region-a"}}, gotQ)
			}
			assert.Equal(t, http.MethodGet, r.Method)
			assert.Equal(t, "/cpu-catalog", r.URL.Path)
			assert.Equal(t, "Bearer k", r.Header.Get("Authorization"))
			assert.Equal(t, "cli", r.Header.Get("X-Daytona-Source"))
			body, err := io.ReadAll(r.Body)
			assert.NoError(t, err)
			validateRequestAgainstContract(t, r, body)
			response := strings.ReplaceAll(cpuCatalogFixture(), "region-b", "region-a")
			w.Header().Set("Content-Type", "application/json")
			validateAgainstContractRequest(t, r, http.StatusOK, []byte(response), "application/json")
			_, _ = io.WriteString(w, response)
		}))
		defer regional.Close()
		home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			homeCalls.Add(1)
			assert.Equal(t, http.MethodGet, r.Method)
			assert.Equal(t, "Bearer k", r.Header.Get("Authorization"))
			if !assert.Equal(t, "/regions", r.URL.Path, "the default API must not be asked for another regional API's catalog") {
				w.WriteHeader(http.StatusConflict)
				_, _ = io.WriteString(w, `{"code":"CONFLICT","message":"region-a is not available at this API URL","source":"DAYTONA_API"}`)
				return
			}
			response := regionsFixture(regionalAPIRow("region-a", regional.URL))
			w.Header().Set("Content-Type", "application/json")
			validateAgainstContractRequest(t, r, http.StatusOK, []byte(response), "application/json")
			_, _ = io.WriteString(w, response)
		}))
		defer home.Close()

		t.Setenv("DAYTONA_CONFIG_DIR", t.TempDir())
		for _, key := range []string{"DAYTONA_API_URL", "DAYTONA_V2_API_URL", "DAYTONA_API_KEY", "DAYTONA_PROFILE", "DAYTONA_ORG", "DAYTONA_PROJECT", "DAYTONA_REGION", "DAYTONA_OIDC_CLIENT_ID", "DAYTONA_YES"} {
			t.Setenv(key, "")
		}
		stdout := &bytes.Buffer{}
		root := NewRoot(stdout, &bytes.Buffer{})
		root.SetArgs([]string{"--api-url", home.URL, "--api-key", "k", "-o", "table", "cpu-type", "list", "--region", "Region-A"})
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		require.NoError(t, root.ExecuteContext(ctx))
		require.Equal(t, int32(1), homeCalls.Load(), "exactly one region lookup on the default API")
		require.Equal(t, int32(1), regionalCalls.Load(), "exactly one catalog request on the regional API")
		lines := strings.Split(strings.TrimSpace(stdout.String()), "\n")
		require.Len(t, lines, 4)
		require.Equal(t, []string{"zen3", "AMD", "Zen", "3", "amd64", "1", "fallback"}, strings.Fields(lines[1]))
		require.Equal(t, []string{"zen5", "AMD", "Zen", "5", "amd64", "3", "exact"}, strings.Fields(lines[2]))
	})

	t.Run("a slug the routing catalog does not list fails before any catalog request", func(t *testing.T) {
		stdout, _, err := runCLIWithOutput(t, "table", []string{"cpu-type", "list", "--region", "mars-1"}, []expectedRequest{routing})
		require.Error(t, err)
		require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		require.Contains(t, err.Error(), `unknown region "mars-1"`)
		require.Contains(t, err.Error(), "daytona region list")
		require.Empty(t, stdout)
	})

	t.Run("without --region the unscoped catalog renders the four columns", func(t *testing.T) {
		stdout, _, err := runCLIWithOutput(t, "table", []string{"cpu-type", "list"}, []expectedRequest{{method: http.MethodGet, path: "/cpu-catalog", status: 200, response: `{"regions":[],"arches":[{"id":"amd64","available":false,"region_ids":[]}],"cpu_types":[{"id":"zen5","name":"Zen 5","cpu_arch":"amd64","tier":1,"available":false,"exact_region_ids":[],"fallback_region_ids":[]}]}`}})
		require.NoError(t, err)
		lines := strings.Split(strings.TrimSpace(stdout), "\n")
		require.Len(t, lines, 2)
		require.Equal(t, []string{"ID", "NAME", "ARCH", "TIER"}, strings.Fields(lines[0]))
		require.Equal(t, []string{"zen5", "Zen", "5", "amd64", "1"}, strings.Fields(lines[1]))
	})

	t.Run("help names both forms and no --limit", func(t *testing.T) {
		long := newCPUTypeListCommand().Commands()[0].Long
		require.Contains(t, long, "daytona cpu-type list\n")
		require.Contains(t, long, "daytona cpu-type list --region")
		require.NotContains(t, long, "--limit")
	})
}

func TestImageBuild_multipartRequest(t *testing.T) {
	root := t.TempDir()
	contextPath := filepath.Join(root, "context.tar")
	require.NoError(t, os.WriteFile(contextPath, []byte("tar-bytes"), 0o600))
	stdout, _, err := runCLI(t,
		[]string{"image", "build", "img", "--dockerfile", "Dockerfile", "--context", contextPath, "--no-wait"},
		[]expectedRequest{{
			method:      http.MethodPost,
			path:        "/images",
			contentType: "multipart/form-data",
			status:      202,
			response:    `{"id":"11111111-1111-1111-1111-111111111111","status":"pending"}`,
			check: func(t *testing.T, r *http.Request, body []byte) {
				_, params, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
				assert.NoError(t, err)
				reader := multipart.NewReader(bytes.NewReader(body), params["boundary"])
				parts := map[string]string{}
				for {
					part, partErr := reader.NextPart()
					if partErr == io.EOF {
						break
					}
					assert.NoError(t, partErr)
					data, readErr := io.ReadAll(part)
					assert.NoError(t, readErr)
					parts[part.FormName()] = string(data)
				}
				assert.Equal(t, "img", parts["name"])
				assert.Equal(t, "Dockerfile", parts["dockerfile"])
				assert.Equal(t, "tar-bytes", parts["context"])
			},
		}},
	)
	require.NoError(t, err)
	require.Equal(t, "pending", requireJSONStdout(t, stdout)["status"])
}

func TestCommandHelpers_coverPagingAndTables(t *testing.T) {
	list := wholeList([]int{1, 2, 3})
	require.Len(t, list.Items, 3)
	require.Empty(t, list.NextCursor)
	require.Equal(t, "2026-09-13T00:00:00Z", formatTimePtr(timePtr(time.Date(2026, 9, 13, 0, 0, 0, 0, time.UTC))))
	require.Equal(t, "x", formatPtr(stringPtrForTest("x")))
	collected, err := collectPaged(context.Background(), "", func(_ context.Context, cursor string) (output.List[int], error) {
		if cursor == "" {
			return output.List[int]{Items: []int{1}, NextCursor: "n1"}, nil
		}
		return output.List[int]{Items: []int{2}}, nil
	})
	require.NoError(t, err)
	require.Equal(t, []int{1, 2}, collected.Items)
	createdAt := time.Date(2026, 9, 13, 0, 0, 0, 0, time.UTC)
	table := sandboxListTable([]gen.SandboxListItem{{Id: "id", Name: "box", Class: "linux", Status: "running", RegionId: "region-d", Image: &gen.ImageReference{Id: "img_123", Name: stringPtrForTest("python:3.12")}, CreatedAt: &createdAt}})
	require.Len(t, table.Rows, 1)
	trimmed := trimSandboxPage(output.List[gen.SandboxListItem]{Items: []gen.SandboxListItem{{RegionId: "a", Cursor: "c1"}, {RegionId: "a", Cursor: "c2"}}, NextCursor: "a:n1"}, regionalPageCursor{requested: map[string]sandboxCursorState{"a": {cursor: "start"}}}, 1)
	require.Equal(t, "a:c1", trimmed.NextCursor)
	require.Equal(t, "a:c", joinSandboxCursorMap(map[string]sandboxCursorState{"a": {cursor: "c"}}))
	_, err = sandboxLogsPath(gen.Sandbox{Id: "id", Class: "unsupported"}, false)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
	_, err = sandboxLogsPath(gen.Sandbox{Id: "id", Class: "unsupported"}, true)
	require.Equal(t, cerr.ExitConflict, cerr.ExitCode(err))
	for _, entrypoint := range []bool{false, true} {
		path, err := sandboxLogsPath(gen.Sandbox{Id: "id", Class: "macos"}, entrypoint)
		require.NoError(t, err)
		require.Equal(t, map[bool]string{false: "/sandboxes/id/console-logs", true: "/sandboxes/id/logs"}[entrypoint], path)
	}
	require.Equal(t, "42", strconv.Itoa(42))
}

func TestCollectPaged_repeatedCursorFails(t *testing.T) {
	_, err := collectPaged(context.Background(), "", func(_ context.Context, _ string) (output.List[int], error) {
		return output.List[int]{Items: []int{1}, NextCursor: "loop"}, nil
	})
	if err == nil || cerr.ExitCode(err) != cerr.ExitFailure || !strings.Contains(err.Error(), "pagination cursor repeated") {
		t.Fatalf("expected repeated-cursor failure, got %v", err)
	}
}

func timePtr(v time.Time) *time.Time { return &v }

func stringPtrForTest(v string) *string { return &v }

func TestRegionListRendersTheTreeInAPIOrder(t *testing.T) {
	body := `{"regions":[` +
		`{"id":"earth","name":"Earth","available":true,"parent_region_ids":[]},` +
		`{"id":"group-a","name":"Group-A","available":true,"parent_region_ids":["earth"]},` +
		`{"id":"group-b","name":"Group-B","available":false,"parent_region_ids":["earth","group-a"]},` +
		`{"id":"region-b","name":"Region-B","available":true,"parent_region_ids":["earth","group-a","group-b"],"api_base_url":"https://api.example.com"}],"default_region_id":null}`
	stdout, _, err := runCLIWithOutput(t, "table", []string{"region", "list"}, []expectedRequest{{method: http.MethodGet, path: "/regions", status: 200, response: body}})
	require.NoError(t, err)
	lines := strings.Split(strings.TrimSpace(stdout), "\n")
	require.Len(t, lines, 5)
	require.Equal(t, []string{"NAME", "ID", "PRICE", "MULTIPLIER", "AVAILABLE"}, strings.Fields(lines[0]))
	// A static credential cannot read the billing route: the column is "-".
	require.Equal(t, []string{"Earth", "earth", "-", "true"}, strings.Fields(lines[1]))
	require.Equal(t, []string{"Group-B", "group-b", "-", "false"}, strings.Fields(lines[3]))
	require.Equal(t, []string{"Region-B", "region-b", "-", "true"}, strings.Fields(lines[4]))
	// Each name is indented two spaces per region it is inside.
	for i, indent := range []string{"Earth", "  Group-A", "    Group-B", "      Region-B"} {
		require.True(t, strings.HasPrefix(lines[i+1], indent+" "), "row %d: %q", i+1, lines[i+1])
	}
}

// Under a login the list reads the selected organization's multipliers: a
// region with its own entry shows it, one without is billed at earth's, and a
// refused read leaves every row "-" instead of failing the list.
func TestRegionListShowsPriceMultipliersUnderALogin(t *testing.T) {
	regions := map[string]any{"regions": []any{
		map[string]any{"id": "earth", "name": "Earth", "available": true, "parent_region_ids": []string{}},
		map[string]any{"id": "group-a", "name": "Group-A", "available": true, "parent_region_ids": []string{"earth"}},
		map[string]any{"id": "region-b", "name": "Region-B", "available": true, "parent_region_ids": []string{"earth", "group-a"}},
	}, "default_region_id": nil}
	multipliers := map[string]any{"region_multipliers": []any{
		map[string]any{"region_id": "earth", "multiplier": 1},
		map[string]any{"region_id": "region-b", "multiplier": 1.15},
	}}

	t.Run("table", func(t *testing.T) {
		f := newFakeAPI(t)
		f.on("GET", "/orgs", 200, organizationsFixture())
		f.on("POST", "/projects/"+testProjP1+"/api-keys", 201, apiKeyFixture("8", testProjP1, "dtn_minted", []string{"read:sandboxes"}))
		f.on("GET", "/regions", 200, regions)
		f.on("GET", "/orgs/"+testOrgP+"/billing/region-multipliers", 200, multipliers)
		d := cfgDir(t)
		oidcProfile(t, d, f.srv.URL, time.Hour)

		out, errb, code := run(t, d, nil, "region", "list", "-o", "table")

		require.Equal(t, 0, code, errb)
		lines := strings.Split(strings.TrimSpace(out), "\n")
		require.Len(t, lines, 4)
		require.Equal(t, []string{"Earth", "earth", "1.0", "true"}, strings.Fields(lines[1]))
		require.Equal(t, []string{"Group-A", "group-a", "1.0", "true"}, strings.Fields(lines[2]))
		require.Equal(t, []string{"Region-B", "region-b", "1.15", "true"}, strings.Fields(lines[3]))
	})

	t.Run("json", func(t *testing.T) {
		f := newFakeAPI(t)
		f.on("GET", "/orgs", 200, organizationsFixture())
		f.on("POST", "/projects/"+testProjP1+"/api-keys", 201, apiKeyFixture("8", testProjP1, "dtn_minted", []string{"read:sandboxes"}))
		f.on("GET", "/regions", 200, regions)
		f.on("GET", "/orgs/"+testOrgP+"/billing/region-multipliers", 200, multipliers)
		d := cfgDir(t)
		oidcProfile(t, d, f.srv.URL, time.Hour)

		out, errb, code := run(t, d, nil, "region", "list", "-o", "json")

		require.Equal(t, 0, code, errb)
		var got struct {
			Items []struct {
				ID              string   `json:"id"`
				ParentRegionIDs []string `json:"parent_region_ids"`
				PriceMultiplier *float64 `json:"price_multiplier"`
			} `json:"items"`
		}
		require.NoError(t, json.Unmarshal([]byte(out), &got))
		require.Len(t, got.Items, 3)
		require.Equal(t, "region-b", got.Items[2].ID)
		require.Equal(t, []string{"earth", "group-a"}, got.Items[2].ParentRegionIDs)
		require.NotNil(t, got.Items[2].PriceMultiplier)
		require.InDelta(t, 1.15, *got.Items[2].PriceMultiplier, 1e-9)
	})

	t.Run("refused read", func(t *testing.T) {
		f := newFakeAPI(t)
		f.on("GET", "/orgs", 200, organizationsFixture())
		f.on("POST", "/projects/"+testProjP1+"/api-keys", 201, apiKeyFixture("8", testProjP1, "dtn_minted", []string{"read:sandboxes"}))
		f.on("GET", "/regions", 200, regions)
		f.on("GET", "/orgs/"+testOrgP+"/billing/region-multipliers", 403, `{"code":"FORBIDDEN","message":"permission denied","source":"DAYTONA_API"}`)
		d := cfgDir(t)
		oidcProfile(t, d, f.srv.URL, time.Hour)

		out, errb, code := run(t, d, nil, "region", "list", "-o", "table")

		require.Equal(t, 0, code, errb)
		lines := strings.Split(strings.TrimSpace(out), "\n")
		require.Equal(t, []string{"Region-B", "region-b", "-", "true"}, strings.Fields(lines[3]))

		// Structured output carries null, not "-".
		out, errb, code = run(t, d, nil, "region", "list", "-o", "json")
		require.Equal(t, 0, code, errb)
		require.Contains(t, out, `"price_multiplier": null`)
		out, errb, code = run(t, d, nil, "region", "list", "-o", "yaml")
		require.Equal(t, 0, code, errb)
		require.Contains(t, out, "price_multiplier: null")
	})

	t.Run("billing never answers", func(t *testing.T) {
		previous := regionMultiplierTimeout
		regionMultiplierTimeout = 200 * time.Millisecond
		t.Cleanup(func() { regionMultiplierTimeout = previous })
		f := newFakeAPI(t)
		f.on("GET", "/orgs", 200, organizationsFixture())
		f.on("POST", "/projects/"+testProjP1+"/api-keys", 201, apiKeyFixture("8", testProjP1, "dtn_minted", []string{"read:sandboxes"}))
		f.on("GET", "/regions", 200, regions)
		f.handle("GET", "/orgs/"+testOrgP+"/billing/region-multipliers", func(_ http.ResponseWriter, r *http.Request) {
			<-r.Context().Done()
		})
		d := cfgDir(t)
		oidcProfile(t, d, f.srv.URL, time.Hour)

		started := time.Now()
		out, errb, code := run(t, d, nil, "region", "list", "-o", "table")

		require.Equal(t, 0, code, errb)
		require.Less(t, time.Since(started), 5*time.Second, "the lookup is bounded by regionMultiplierTimeout")
		lines := strings.Split(strings.TrimSpace(out), "\n")
		require.Equal(t, []string{"Region-B", "region-b", "-", "true"}, strings.Fields(lines[3]))
	})
}

func TestFormatMultiplier(t *testing.T) {
	value := func(v float64) *float64 { return &v }
	for _, tc := range []struct {
		in   *float64
		want string
	}{
		{nil, "-"},
		{value(1), "1.0"},
		{value(1.15), "1.15"},
		{value(1.75), "1.75"},
		{value(2), "2.0"},
		{value(0.5), "0.5"},
		{value(math.NaN()), "1.0"},
		{value(math.Inf(1)), "1.0"},
	} {
		require.Equal(t, tc.want, formatMultiplier(tc.in))
	}
}
