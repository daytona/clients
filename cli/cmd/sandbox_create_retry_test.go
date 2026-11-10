// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestSandboxCreate_rejectsInvalidReturnedID(t *testing.T) {
	for _, id := range []string{"", " \t", ".", ".."} {
		for _, wait := range []bool{false, true} {
			t.Run(id+"/wait="+map[bool]string{false: "false", true: "true"}[wait], func(t *testing.T) {
				// Given: a deliberately malformed create acknowledgement.
				t.Setenv("DAYTONA_TEST_SKIP_CONTRACT", "1")
				response, err := json.Marshal(map[string]string{"id": id, "name": "box", "status": "pending"})
				require.NoError(t, err)
				args := []string{"sandbox", "create"}
				if !wait {
					args = append(args, "--no-wait")
				}
				// When: creating a sandbox must not follow that id or print success.
				stdout, _, err := runCLI(t, args, []expectedRequest{{method: http.MethodPost, path: "/sandboxes", bodyJSON: `{}`, status: 202, response: string(response)}})
				// Then
				require.Error(t, err)
				require.Equal(t, cerr.ExitFailure, cerr.ExitCode(err))
				require.ErrorContains(t, err, "id")
				require.Empty(t, stdout)
			})
		}
	}
}

func TestSandboxCreate_interruptionPreservesRetryKey(t *testing.T) {
	// Given: a create POST canceled before its answer arrives.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := cerr.FromContext(ctx)
	// When
	got := withRetryKeyHint(err, "create-review-key")
	// Then: interruption remains detectable and the retry can reuse the key.
	require.ErrorIs(t, got, context.Canceled)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(got))
	require.Contains(t, cerr.DetailsOf(got).Hint, "--idempotency-key create-review-key")
}

func TestSandboxCreate_interruptedPOSTReportsRetryKey(t *testing.T) {
	// Given: the server receives the create but never acknowledges it.
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		assert.Equal(t, http.MethodPost, r.Method)
		assert.Equal(t, "/sandboxes", r.URL.Path)
		assert.Equal(t, "interrupted-create-key", r.Header.Get("Idempotency-Key"))
		cancel()
		<-release
	}))
	defer server.Close()
	defer close(release)
	t.Setenv("DAYTONA_CONFIG_DIR", t.TempDir())
	for _, key := range []string{"DAYTONA_PROFILE", "DAYTONA_ORG", "DAYTONA_PROJECT", "DAYTONA_V2_API_URL"} {
		t.Setenv(key, "")
	}
	var stdout, stderr bytes.Buffer
	root := NewRoot(&stdout, &stderr)
	root.SetArgs([]string{"--api-url", server.URL, "--api-key", "k", "-o", "json", "sandbox", "create", "--idempotency-key", "interrupted-create-key"})
	// When
	err := root.ExecuteContext(ctx)
	// Then: preserve interruption and expose the exact key sent in the POST.
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	require.ErrorIs(t, err, context.Canceled)
	require.Contains(t, cerr.DetailsOf(err).Hint, "--idempotency-key interrupted-create-key")
	require.Empty(t, stdout.String())
}

func TestSandboxCreate_fileServerWaitRejectsNoWait(t *testing.T) {
	for _, args := range [][]string{
		{"sandbox", "create"},
		{"sandbox", "create-from-snapshot", "snap_review"},
		{"sandbox", "create-macos", "--image", "base-macos"},
	} {
		for _, seconds := range []string{"0", "3"} {
			t.Run(args[1]+"/"+seconds, func(t *testing.T) {
				// Given: --file explicitly selects a server wait, including zero.
				path := filepath.Join(t.TempDir(), "create.json")
				require.NoError(t, os.WriteFile(path, []byte(`{"wait_seconds":`+seconds+`}`), 0o600))
				// When
				stdout, _, err := runCLI(t, append(args, "--file", path, "--no-wait"), nil)
				// Then: reject before a request or a macOS purchase confirmation.
				require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
				require.ErrorContains(t, err, "wait_seconds")
				require.ErrorContains(t, err, "--no-wait")
				require.Empty(t, stdout)
			})
		}
	}
}
