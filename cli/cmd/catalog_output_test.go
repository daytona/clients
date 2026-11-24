// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

// `region list` keeps the catalog's default region: JSON and YAML add
// `default_region_id` next to the list envelope (null when the API sends
// null), and the table marks that region.
func TestRegionListKeepsTheDefaultRegion(t *testing.T) {
	body := `{"regions":[` +
		`{"id":"earth","name":"Earth","available":true,"parent_region_ids":[]},` +
		`{"id":"region-b","name":"Region-B","available":true,"parent_region_ids":["earth"]}],"default_region_id":"region-b"}`
	list := expectedRequest{method: http.MethodGet, path: "/regions", status: 200, response: body}

	stdout, _, err := runCLI(t, []string{"region", "list"}, []expectedRequest{list})
	require.NoError(t, err)
	var got map[string]json.RawMessage
	require.NoError(t, json.Unmarshal([]byte(stdout), &got))
	require.JSONEq(t, `"region-b"`, string(got["default_region_id"]))
	require.JSONEq(t, `false`, string(got["incomplete"]))
	var items []map[string]any
	require.NoError(t, json.Unmarshal(got["items"], &items))
	require.Len(t, items, 2)
	require.Equal(t, "region-b", items[1]["id"])
	require.Contains(t, items[1], "price_multiplier")

	stdout, _, err = runCLIWithOutput(t, "yaml", []string{"region", "list"}, []expectedRequest{list})
	require.NoError(t, err)
	require.Contains(t, stdout, "default_region_id: region-b")

	stdout, _, err = runCLIWithOutput(t, "table", []string{"region", "list"}, []expectedRequest{list})
	require.NoError(t, err)
	lines := strings.Split(strings.TrimSpace(stdout), "\n")
	require.Len(t, lines, 3)
	require.Equal(t, []string{"Earth", "earth", "-", "true"}, strings.Fields(lines[1]))
	require.Equal(t, []string{"Region-B", "(default)", "region-b", "-", "true"}, strings.Fields(lines[2]))

	stdout, _, err = runCLI(t, []string{"region", "list"}, []expectedRequest{{method: http.MethodGet, path: "/regions", status: 200, response: regionsFixture()}})
	require.NoError(t, err)
	require.NoError(t, json.Unmarshal([]byte(stdout), &got))
	require.JSONEq(t, `null`, string(got["default_region_id"]))
}

// `cpu-type list` without --region also carries the catalog's regions and
// arches, as the API sent them, and lists the region defaults under the table.
func TestCPUTypeListKeepsTheCatalogDefaults(t *testing.T) {
	catalog := expectedRequest{method: http.MethodGet, path: "/cpu-catalog", status: 200, response: cpuCatalogFixture()}

	stdout, _, err := runCLI(t, []string{"cpu-type", "list"}, []expectedRequest{catalog})
	require.NoError(t, err)
	var fixture map[string]json.RawMessage
	require.NoError(t, json.Unmarshal([]byte(cpuCatalogFixture()), &fixture))
	want, err := json.Marshal(map[string]any{"items": fixture["cpu_types"], "incomplete": false, "regions": fixture["regions"], "arches": fixture["arches"]})
	require.NoError(t, err)
	require.JSONEq(t, string(want), stdout)

	stdout, _, err = runCLIWithOutput(t, "table", []string{"cpu-type", "list"}, []expectedRequest{catalog})
	require.NoError(t, err)
	lines := cpuTableLines(t, stdout, "region-b: amd64, zen5")
	require.Equal(t, []string{"ID", "NAME", "ARCH", "TIER"}, strings.Fields(lines[0]))
	require.Len(t, lines, 4)
}

// A region default is server text: a line break in it cannot add a line of
// its own under the table.
func TestCPUTypeListRegionDefaultsStayOnOneLinePerRegion(t *testing.T) {
	body := `{"regions":[{"id":"region-b\nfake: line","default_arch":"amd64","default_type":"zen5\nx"}],"arches":[],"cpu_types":[]}`
	stdout, _, err := runCLIWithOutput(t, "table", []string{"cpu-type", "list"}, []expectedRequest{{method: http.MethodGet, path: "/cpu-catalog", status: 200, response: body}})
	require.NoError(t, err)
	require.Equal(t, "No results.\n\nDefaults for a create that names no CPU type:\n  region-b fake: line: amd64, zen5 x\n", stdout)
}

// `sandbox usage --history` prints the bucket size and one table row per
// period, every period in order; before, the table showed only the request's
// `history true` switch. JSON stays the API's body.
func TestSandboxUsageHistoryTableShowsThePeriods(t *testing.T) {
	const id = "11111111-1111-1111-1111-111111111111"
	detail := expectedRequest{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "linux", "running")}
	body := usageHistoryFixture(id)
	body["periods"] = append(body["periods"].([]map[string]any), map[string]any{
		"start_at": "2026-09-13T00:01:00Z", "duration_ms": 30500, "cpu_core_seconds": 0.25,
		"memory_byte_seconds": 1, "memory_peak_bytes": 2048, "disk_used_bytes": 4096, "disk_read_bytes": 10,
		"disk_written_bytes": 20, "network_received_bytes": 30, "network_sent_bytes": 40,
	})
	history := expectedRequest{method: http.MethodGet, path: "/sandboxes/" + id + "/usage/history", status: 200, response: fixtureJSON(body)}

	stdout, _, err := runCLIWithOutput(t, "table", []string{"sandbox", "usage", id, "--history"}, []expectedRequest{detail, history})
	require.NoError(t, err)
	lines := strings.Split(strings.TrimSpace(stdout), "\n")
	require.Len(t, lines, 5)
	require.Equal(t, "Resolution: one row per minute", lines[0])
	require.Empty(t, lines[1])
	require.Equal(t, []string{"START", "DURATION", "CPU", "CORE-SECONDS", "MEMORY", "PEAK", "BYTES", "DISK", "USED", "BYTES", "DISK", "READ", "BYTES", "DISK", "WRITTEN", "BYTES", "NET", "RECEIVED", "BYTES", "NET", "SENT", "BYTES"}, strings.Fields(lines[2]))
	require.Equal(t, []string{"2026-09-13T00:00:00Z", "1m0s", "1", "512", "1024", "0", "0", "0", "0"}, strings.Fields(lines[3]))
	require.Equal(t, []string{"2026-09-13T00:01:00Z", "30.5s", "0.25", "2048", "4096", "10", "20", "30", "40"}, strings.Fields(lines[4]))
	require.NotContains(t, stdout, "history")

	stdout, _, err = runCLI(t, []string{"sandbox", "usage", id, "--history"}, []expectedRequest{detail, history})
	require.NoError(t, err)
	require.JSONEq(t, fixtureJSON(body), stdout)

	unavailable := history
	unavailable.response = `{"available":false,"sandbox_id":"` + id + `","periods":[]}`
	stdout, _, err = runCLIWithOutput(t, "table", []string{"sandbox", "usage", id, "--history"}, []expectedRequest{detail, unavailable})
	require.NoError(t, err)
	require.Equal(t, "Usage history is not available for sandbox "+id+".\n", stdout)

	empty := history
	empty.response = `{"available":true,"resolution":"hour","sandbox_id":"` + id + `","periods":[]}`
	stdout, _, err = runCLIWithOutput(t, "table", []string{"sandbox", "usage", id, "--history"}, []expectedRequest{detail, empty})
	require.NoError(t, err)
	require.Equal(t, "Resolution: one row per hour\n\nNo results.\n", stdout)
}
