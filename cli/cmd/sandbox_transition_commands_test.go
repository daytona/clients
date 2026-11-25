// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"net/http"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/stretchr/testify/require"
)

func TestSandboxArtifactCommands_retryOnlyTransitionRefusals(t *testing.T) {
	id := "11111111-1111-1111-1111-111111111111"
	createdID := "22222222-2222-2222-2222-222222222222"
	for _, operation := range []struct {
		name, suffix, body, response string
		args                         []string
	}{
		{"fork", "fork", `{"name":"copy"}`, `{"toolbox_proxy_url":"https://proxy.example/test-tools","forked_from":"` + id + `","id":"` + createdID + `","status":"pending"}`, []string{"sandbox", "fork", id, "--name", "copy"}},
		{"capture", "images", `{"name":"copy"}`, `{"id":"` + createdID + `","status":"pending"}`, []string{"image", "capture", id, "--name", "copy"}},
		{"snapshot", "snapshots", `{"name":"copy"}`, `{"id":"` + createdID + `","status":"pending"}`, []string{"snapshot", "create", id, "--name", "copy"}},
	} {
		t.Run(operation.name, func(t *testing.T) {
			for _, code := range []string{"SANDBOX_TRANSITION_IN_PROGRESS", "CONFLICT"} {
				t.Run(code, func(t *testing.T) {
					// Given
					path := "/sandboxes/" + id + "/" + operation.suffix
					requests := []expectedRequest{
						{method: http.MethodGet, path: "/sandboxes/" + id, status: 200, response: sandboxDetailFixture(id, "linux", "running")},
						{method: http.MethodPost, path: path, status: 409, bodyJSON: operation.body, response: `{"code":"` + code + `","message":"settling","source":"DAYTONA_API","retry_after_seconds":1}`},
					}
					if code == "SANDBOX_TRANSITION_IN_PROGRESS" {
						requests = append(requests, expectedRequest{method: http.MethodPost, path: path, status: 202, bodyJSON: operation.body, response: operation.response})
						if operation.name == "fork" {
							requests = append(requests, expectedRequest{method: http.MethodGet, path: "/sandboxes/" + createdID, status: 200, response: sandboxDetailFixture(createdID, "linux", "pending")})
						}
					}
					// When
					stdout, _, err := runCLIWithOptions(t, runCLIOptions{contextTimeout: 5 * time.Second}, append(operation.args, "--no-wait"), requests)
					// Then
					if code == "SANDBOX_TRANSITION_IN_PROGRESS" {
						require.NoError(t, err)
						require.Equal(t, createdID, requireJSONStdout(t, stdout)["id"])
					} else {
						var refusal *api.APIError
						require.ErrorAs(t, err, &refusal)
						require.Equal(t, "CONFLICT", refusal.Code)
					}
				})
			}
		})
	}
}
