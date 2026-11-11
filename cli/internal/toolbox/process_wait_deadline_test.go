// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	tbgen "github.com/daytona/clients/cli/gen/toolbox"
	"github.com/stretchr/testify/require"
)

func TestWaitProcess_returnsTerminalTimeout_whenExecutionDeadlineExpired(t *testing.T) {
	for _, budget := range []time.Duration{0, time.Second} {
		t.Run(budget.String(), func(t *testing.T) {
			// Given: timed_out also denotes a process's own execution deadline,
			// not just an expired long poll. Its record distinguishes the two.
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if r.URL.Path == "/processes/proc/wait" {
					_, _ = io.WriteString(w, `{"reason":"timed_out","signal":"SIGKILL"}`)
					return
				}
				_, _ = io.WriteString(w, `{"id":"proc","state":"terminal","kind":"pty","stdin":"open","cwd":"/","keepLogs":"until_cleanup","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","reason":"timed_out","signal":"SIGKILL"}`)
			}))
			defer srv.Close()
			ctx, cancel := context.WithTimeout(t.Context(), 2*time.Second)
			defer cancel()
			// When
			result, err := newToolboxClient(srv).WaitProcess(ctx, "proc", budget)
			// Then
			require.NoError(t, err)
			require.NotNil(t, result.V.Reason)
			require.Equal(t, tbgen.ReasonTimedOut, *result.V.Reason)
			require.NotNil(t, result.V.Signal)
			require.Equal(t, "KILL", *result.V.Signal)
		})
	}
}
