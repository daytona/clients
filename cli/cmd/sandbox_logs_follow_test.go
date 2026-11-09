// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/spf13/cobra"
	"github.com/stretchr/testify/require"
)

func TestSandboxLogsFollow_jsonOutputsNDJSONChunks(t *testing.T) {
	oldInterval := sandboxLogsPollInterval
	sandboxLogsPollInterval = 10 * time.Millisecond
	defer func() { sandboxLogsPollInterval = oldInterval }()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var calls int
	seenSecond := make(chan struct{})
	go func() {
		<-seenSecond
		time.Sleep(5 * time.Millisecond)
		cancel()
	}()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		switch calls {
		case 1:
			_, _ = w.Write([]byte("first\n"))
		case 2:
			_, _ = w.Write([]byte("first\nsecond\n"))
			close(seenSecond)
		default:
			// A slow machine may poll again before the cancel lands: the
			// same snapshot again adds nothing.
			_, _ = w.Write([]byte("first\nsecond\n"))
			cancel()
		}
	}))
	defer srv.Close()
	cmd := &cobra.Command{}
	cmd.SetContext(ctx)
	stdout := new(strings.Builder)
	stderr := new(strings.Builder)
	_, err := readSandboxLogs(cmd, api.New(srv.URL, "tok", "test"), "/logs", true, output.FormatJSON, stdout, stderr)
	require.Error(t, err)
	require.Equal(t, cerr.ExitInterrupted, cerr.ExitCode(err))
	lines := strings.Split(strings.TrimSpace(stdout.String()), "\n")
	require.Len(t, lines, 2)
	var first, second map[string]string
	require.NoError(t, json.Unmarshal([]byte(lines[0]), &first))
	require.NoError(t, json.Unmarshal([]byte(lines[1]), &second))
	require.Equal(t, map[string]string{"data": "first\n"}, first)
	require.Equal(t, map[string]string{"data": "second\n"}, second)
	require.Empty(t, stderr.String())
}

func TestSandboxLogsFollow_yamlRejected(t *testing.T) {
	api := newFakeAPI(t)
	api.on("GET", "/sandboxes/11111111-1111-1111-1111-111111111111", 200, sandboxDetailFixture("11111111-1111-1111-1111-111111111111", "linux", "running"))
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "-o", "yaml", "sandbox", "logs", "11111111-1111-1111-1111-111111111111", "--follow")
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, stderr, "--output yaml is not supported with --follow")
	require.Empty(t, api.requests, "refused before any request")
}

func TestSandboxPreviewURL_ttlRequiresSigned(t *testing.T) {
	api := newFakeAPI(t)
	_, stderr, code := run(t, cfgDir(t), nil, "--api-url", api.srv.URL, "--api-key", "k", "sandbox", "port", "preview-url", "sbx", "8080", "--ttl", "5m")
	require.Equal(t, cerr.ExitUsage, code)
	require.Contains(t, stderr, "--ttl requires --signed")
	require.Empty(t, api.requests)
}

func TestNextSandboxLogsPollInterval(t *testing.T) {
	require.Equal(t, 2*time.Second, nextSandboxLogsPollInterval(10*time.Second, true))
	require.Equal(t, 5*time.Second, nextSandboxLogsPollInterval(2*time.Second, false))
	require.Equal(t, 10*time.Second, nextSandboxLogsPollInterval(5*time.Second, false))
}
