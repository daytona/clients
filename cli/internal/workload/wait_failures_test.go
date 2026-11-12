// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestResourceWait_keepsTimeoutAdvice_whenBudgetExpires(t *testing.T) {
	for _, resource := range []string{"image", "snapshot"} {
		t.Run(resource, func(t *testing.T) {
			// Given an already spent local wait budget.
			client := api.New("https://api.example", "fixture", "test")
			// When waiting for the pending resource.
			var err error
			if resource == "image" {
				_, err = WaitForImage(t.Context(), client, "pending", 0)
			} else {
				_, err = WaitForSnapshot(t.Context(), client, "pending", 0)
			}
			// Then the outer error preserves the actionable hint.
			require.Contains(t, cerr.DetailsOf(err).Hint, "--timeout")
		})
	}
}

func TestWaitForSnapshot_reportsFailureDetail_whenServerRecordsError(t *testing.T) {
	// Given a failed snapshot with a recorded reason.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"id":"snap","status":"failed","error":"disk read failed"}`))
	}))
	defer srv.Close()
	client := api.New(srv.URL, "fixture", "test")
	// When waiting for readiness.
	_, err := WaitForSnapshot(t.Context(), client, "snap", time.Minute)
	// Then the failure explains why the snapshot failed.
	require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
	require.ErrorContains(t, err, "disk read failed")
}
