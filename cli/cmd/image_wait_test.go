// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestResourceWait_reportsPendingID_whenDeadlinePasses(t *testing.T) {
	const sandboxID = "11111111-1111-1111-1111-111111111111"
	for _, tc := range []struct {
		name string
		args []string
		kind string
	}{
		{"import", []string{"image", "create", "pending", "--ref", "python:3.12"}, "image"},
		{"build", []string{"image", "build", "pending"}, "image"},
		{"capture", []string{"image", "capture", sandboxID}, "image"},
		{"snapshot", []string{"snapshot", "create", sandboxID, "--name", "pending"}, "snapshot"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			// Given an accepted operation whose long-poll never settles.
			const pendingID = "22222222-2222-2222-2222-222222222222"
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				switch {
				case r.Method == http.MethodPost:
					_, _ = io.Copy(io.Discard, r.Body)
					w.WriteHeader(http.StatusAccepted)
					_, _ = io.WriteString(w, `{"id":"`+pendingID+`","status":"pending"}`)
				case r.URL.Path == "/sandboxes/"+sandboxID:
					_, _ = io.WriteString(w, sandboxDetailFixture(sandboxID, "linux", "running"))
				default:
					<-r.Context().Done()
				}
			}))
			defer server.Close()
			args := append([]string{"--api-url", server.URL, "--api-key", "k"}, tc.args...)
			if tc.name == "build" {
				dir := t.TempDir()
				writeContextFiles(t, dir, map[string]string{"Dockerfile": "FROM scratch"})
				args = append(args, "--context", dir)
			}
			args = append(args, "--timeout", "100ms")
			// When the command's wait deadline elapses.
			_, stderr, code := run(t, cfgDir(t), nil, args...)
			// Then the error provides the recovery handle and keeps exit 8.
			require.Equal(t, cerr.ExitTimeout, code, stderr)
			require.Contains(t, stderr, tc.kind+" "+pendingID)
			require.Contains(t, stderr, "daytona "+tc.kind+" get "+pendingID)
		})
	}
}
