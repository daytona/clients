// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

// Ctrl+C during a regional list is an interruption (exit 130), not a failed
// region (exit 7).
func TestRegionalLists_callerCancellationExitsInterrupted(t *testing.T) {
	for _, resource := range []string{"sandbox", "image"} {
		t.Run(resource, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			good := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if writeSearchNotConfigured(w, r) {
					return
				}
				writeListItems(t, w, resource, []listTestItem{{"one", "east", "c1"}}, "")
			}))
			defer good.Close()
			slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if writeSearchNotConfigured(w, r) {
					return
				}
				cancel()
				<-r.Context().Done()
			}))
			defer slow.Close()
			catalog := []gen.Region{listTestRegion("east", good.URL), listTestRegion("west", slow.URL)}
			home := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if writeSearchNotConfigured(w, r) {
					return
				}
				if err := json.NewEncoder(w).Encode(gen.RegionList{Regions: catalog}); err != nil {
					t.Error(err)
				}
			}))
			defer home.Close()
			stdout, stderr := &bytes.Buffer{}, &bytes.Buffer{}
			root := NewRoot(stdout, stderr)
			root.SetArgs([]string{"--api-url", home.URL, "--api-key", "k", "-o", "json", resource, "list"})
			err := root.ExecuteContext(ctx)
			require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err), "err=%v stderr=%s", err, stderr)
			require.Empty(t, stdout.String())
		})
	}
}
