// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestExec_forwardsInterruptOnly_whenParentCancelled(t *testing.T) {
	for _, interrupt := range []bool{true, false} {
		name := "client wait deadline"
		if interrupt {
			name = "caller interrupt"
		}
		t.Run(name, func(t *testing.T) {
			// Given
			stdin, err := os.CreateTemp(t.TempDir(), "stdin")
			require.NoError(t, err)
			oldStdin := os.Stdin
			os.Stdin = stdin
			t.Cleanup(func() { os.Stdin = oldStdin; require.NoError(t, stdin.Close()) })
			ctx, cancel := context.WithCancel(t.Context())
			defer cancel()
			var signals atomic.Int32
			serverURL := ""
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				switch r.URL.Path {
				case "/sandboxes/sbx":
					_, _ = io.WriteString(w, strings.ReplaceAll(toolboxSandboxDetailFixture("sbx", "linux", "running"), "__SERVER__", serverURL))
				case "/test-tools/sbx/processes":
					_, _ = io.WriteString(w, `{"id":"proc","state":"running","kind":"exec","stdin":"open","cwd":"/","keepLogs":"on_exit_ttl","truncatedHead":false,"createdAt":"2026-09-13T00:00:00Z","logs":{"frames":[],"nextCursor":"c0","truncatedHead":false,"eof":false}}`)
				case "/test-tools/sbx/processes/proc/stdin":
					w.WriteHeader(http.StatusNoContent)
				case "/test-tools/sbx/processes/proc/logs":
					w.Header().Set("Content-Type", "text/event-stream")
					w.(http.Flusher).Flush()
					if interrupt {
						cancel()
					}
					<-r.Context().Done()
				case "/test-tools/sbx/processes/proc/signals":
					var signal struct {
						Signal string `json:"signal"`
						After  int64  `json:"escalateAfterMs"`
						To     string `json:"escalateTo"`
					}
					assert.NoError(t, json.NewDecoder(r.Body).Decode(&signal))
					assert.Equal(t, "SIGINT", signal.Signal)
					assert.Equal(t, int64(1000), signal.After)
					assert.Equal(t, "SIGKILL", signal.To)
					signals.Add(1)
					w.WriteHeader(http.StatusNoContent)
				default:
					t.Errorf("unexpected request %s", r.URL.Path)
					w.WriteHeader(http.StatusNotFound)
				}
			}))
			defer srv.Close()
			serverURL = srv.URL
			t.Setenv("DAYTONA_CONFIG_DIR", cfgDir(t))
			root := NewRoot(&bytes.Buffer{}, &bytes.Buffer{})
			budget := "1h"
			if !interrupt {
				budget = "100ms"
			}
			root.SetArgs([]string{"--api-url", srv.URL, "--api-key", "k", "sandbox", "exec", "sbx", "--timeout", budget, "--", "sleep", "123"})
			// When
			err = root.ExecuteContext(ctx)
			// Then
			if interrupt {
				require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
				require.Equal(t, int32(1), signals.Load())
			} else {
				require.Equal(t, cerr.ExitTimeout, cerr.ExitCode(err))
				require.Zero(t, signals.Load())
			}
		})
	}
}
