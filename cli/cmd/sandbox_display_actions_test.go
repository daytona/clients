// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"net/url"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/stretchr/testify/require"
)

func TestSandboxDisplay_usesOriginalImage_whenResourceReferenceIsAbsent(t *testing.T) {
	// Given a sandbox created from an original reference, without an image resource.
	ref := "python:3.12"
	detail := gen.Sandbox{ImageRef: &ref}
	item := gen.SandboxListItem{ImageRef: &ref}
	// When rendering each user-facing view.
	views := []string{sandboxListTable([]gen.SandboxListItem{item}).Rows[0][5], sandboxSearchTable([]gen.SandboxListItem{item}).Rows[0][5]}
	for _, row := range sandboxDetailKV(detail) {
		if row[0] == "image" {
			views = append(views, row[1])
		}
	}
	// Then all views retain the original reference.
	require.Equal(t, []string{ref, ref, ref}, views)
}

func TestSandboxDisplay_keepsCPUPrecision_whenCoresAreFractional(t *testing.T) {
	// Given a precise CPU amount.
	detail := gen.Sandbox{Cpu: 0.0625, CoresMax: 0.0625}
	// When rendering its detail rows.
	rows := sandboxDetailKV(detail)
	// Then the CPU row is as precise as the limit row.
	found := map[string]string{}
	for _, row := range rows {
		if row[0] == "cpu" || row[0] == "cores_max" {
			found[row[0]] = row[1]
		}
	}
	require.Equal(t, map[string]string{"cpu": "0.0625", "cores_max": "0.0625"}, found)
}

func TestSandboxSearch_preservesLabelComma_whenFlagIsRepeated(t *testing.T) {
	// Given comma-bearing values and multiple labels.
	f := newFakeAPI(t)
	f.on(http.MethodGet, "/sandboxes/search", 200, `{"sandboxes":[],"total":0,"total_relation":"eq"}`)
	// When searching through the real CLI parser.
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "fixture", "sandbox", "search", "--label", "tags=a,b", "--label", "env=dev")
	// Then both labels arrive unchanged.
	require.Zero(t, code, stderr)
	query, err := url.ParseQuery(findRequest(t, f, http.MethodGet, "/sandboxes/search").Query)
	require.NoError(t, err)
	require.JSONEq(t, `{"tags":"a,b","env":"dev"}`, query.Get("labels"))
}

func TestSandboxSearch_omitsLabels_whenJSONFlagIsBlank(t *testing.T) {
	// Given a whitespace-only environment expansion for --labels.
	f := newFakeAPI(t)
	f.on(http.MethodGet, "/sandboxes/search", 200, `{"sandboxes":[],"total":0,"total_relation":"eq"}`)
	// When searching without any effective labels.
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", f.srv.URL, "--api-key", "fixture", "sandbox", "search", "--labels", " \t ")
	// Then the request contains no labels filter.
	require.Zero(t, code, stderr)
	query, err := url.ParseQuery(findRequest(t, f, http.MethodGet, "/sandboxes/search").Query)
	require.NoError(t, err)
	require.False(t, query.Has("labels"))
}

func TestSandboxAction_printsAcceptedOperation_whenNoWaitReadFails(t *testing.T) {
	// Prepare contract fixtures outside the harness's two-second command budget.
	loadContracts(t)
	for _, verb := range []string{"start", "stop", "pause", "resume", "fork", "delete"} {
		t.Run(verb, func(t *testing.T) {
			// Given a successful action followed by a failed metadata read.
			id := "11111111-1111-1111-1111-111111111111"
			ack := `{"id":"` + id + `","status":"pending"}`
			method, path := http.MethodPost, "/sandboxes/"+id+"/"+verb
			if verb == "delete" {
				method, path = http.MethodDelete, "/sandboxes/"+id
			}
			if verb == "fork" {
				ack = `{"id":"fork-id","status":"pending","forked_from":"` + id + `","toolbox_proxy_url":"https://toolbox.example"}`
			}
			readID := id
			bodyJSON := ""
			if verb == "fork" {
				readID = "fork-id"
				bodyJSON = "null"
			}
			// When requesting acknowledgement without waiting.
			stdout, stderr, err := runCLI(t, []string{"sandbox", verb, id, "--no-wait", "--yes"}, []expectedRequest{
				{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "linux", "stopped")},
				{method: method, path: path, status: 202, response: ack, bodyJSON: bodyJSON},
				{method: http.MethodGet, path: "/sandboxes/" + readID, status: 503, response: `{"message":"temporarily unavailable"}`},
			})
			// Then success retains the accepted identity and warns instead of inviting a retry.
			require.NoError(t, err)
			require.JSONEq(t, ack, stdout)
			require.Contains(t, stderr, "warning:")
		})
	}
}
