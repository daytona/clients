// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

type toolboxExpectedRequest struct {
	method              string
	path                string
	query               url.Values
	status              int
	response            string
	responseContentType string
	auth                bool
	check               func(*testing.T, *http.Request, []byte)
}

type runToolboxCLIOptions struct {
	output string
}

var toolboxEnvMu sync.Mutex

func runToolboxCLI(t *testing.T, args []string, expected []toolboxExpectedRequest) (string, string, error) {
	t.Helper()
	return runToolboxCLIWithOptions(t, runToolboxCLIOptions{output: "json"}, args, expected)
}

func runToolboxCLIWithOutput(t *testing.T, output string, args []string, expected []toolboxExpectedRequest) (string, string, error) {
	t.Helper()
	return runToolboxCLIWithOptions(t, runToolboxCLIOptions{output: output}, args, expected)
}

func runToolboxCLIWithOptions(t *testing.T, opts runToolboxCLIOptions, args []string, expected []toolboxExpectedRequest) (string, string, error) {
	t.Helper()
	var index int
	serverURL := ""
	// The handler runs on the server's goroutine: use assert (t.Errorf), never
	// require (FailNow is only valid on the test goroutine). An unexpected
	// request answers 500 so the CLI side fails too and the test goroutine's
	// own assertions report the mismatch.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if index >= len(expected) {
			t.Errorf("unexpected request %s %s", r.Method, r.URL.String())
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		exp := expected[index]
		index++
		assert.Equal(t, exp.method, r.Method)
		assert.Equal(t, exp.path, r.URL.Path)
		if exp.query != nil {
			assert.Equal(t, exp.query, r.URL.Query())
		}
		if exp.auth {
			assert.Equal(t, "Bearer k", r.Header.Get("Authorization"))
		} else {
			assert.Empty(t, r.Header.Get("Authorization"))
		}
		body, err := io.ReadAll(r.Body)
		if !assert.NoError(t, err) {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		if exp.check != nil {
			exp.check(t, r, body)
		}
		// Same contract harness as the fakeAPI, for both API and toolbox requests.
		validateRequestAgainstContract(t, r, body)
		rendered := strings.ReplaceAll(exp.response, "__SERVER__", serverURL)
		if exp.response != "" {
			contentType := exp.responseContentType
			if contentType == "" {
				contentType = "application/json"
			}
			w.Header().Set("Content-Type", contentType)
			validateAgainstContractRequest(t, r, exp.status, []byte(rendered), contentType)
		}
		w.WriteHeader(exp.status)
		_, _ = io.WriteString(w, rendered)
	}))
	defer server.Close()
	serverURL = server.URL

	toolboxEnvMu.Lock()
	defer toolboxEnvMu.Unlock()
	previous := os.Getenv("DAYTONA_CONFIG_DIR")
	t.Cleanup(func() {
		_ = os.Setenv("DAYTONA_CONFIG_DIR", previous)
	})
	require.NoError(t, os.Setenv("DAYTONA_CONFIG_DIR", t.TempDir()))

	stdout := &bytes.Buffer{}
	stderr := &bytes.Buffer{}
	root := NewRoot(stdout, stderr)
	output := opts.output
	if output == "" {
		output = "json"
	}
	root.SetArgs(append([]string{"--api-url", server.URL, "--api-key", "k", "-o", output}, args...))
	// Cold contract loading under the race detector can exceed two seconds;
	// command-specific wait budgets are still exercised by their own flags.
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	err := root.ExecuteContext(ctx)
	if err == nil {
		require.Equal(t, len(expected), index)
	}
	return stdout.String(), stderr.String(), err
}
