// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"net/http"
	"testing"

	"github.com/stretchr/testify/require"
)

const interpreterInstallFailure = "cannot install ipykernel (is PyPI reachable from this sandbox?): " +
	"uv pip install: exit 1: network unreachable"

func interpreterSetupFailedRoutes(t *testing.T, frames []any) map[string]http.HandlerFunc {
	t.Helper()
	routes := interpreterRunRoutes(t)
	routes["GET /processes/p/wait"] = jsonHandler(t, map[string]any{"reason": "failed"})
	routes["GET /processes/p/logs"] = jsonHandler(t, map[string]any{
		"frames": frames, "nextCursor": "2", "truncatedHead": false, "eof": true,
	})
	return routes
}

func interpreterSetupFailedFrames() []any {
	return []any{
		map[string]any{
			"cursor": "1", "timestamp": 1, "channel": "setup",
			"data": "Preparing the python kernel (first use): installing ipykernel\n",
		},
		map[string]any{
			"cursor": "2", "timestamp": 2, "channel": "setup",
			"data": "code run failed: " + interpreterInstallFailure + "\n",
		},
	}
}

func requireKernelSetupError(t *testing.T, err error) *Error {
	t.Helper()
	require.ErrorIs(t, err, ErrKernelUnavailable)
	require.ErrorIs(t, err, ErrServiceUnavailable)
	var sdkErr *Error
	require.ErrorAs(t, err, &sdkErr)
	require.Equal(t, SourceSDK, sdkErr.Source)
	require.Equal(t, http.StatusServiceUnavailable, sdkErr.StatusCode)
	return sdkErr
}

func TestRunCode_whenKernelSetupFailedReturnsKernelUnavailable(t *testing.T) {
	// Given
	fake := newFakeServer(t, interpreterSetupFailedRoutes(t, interpreterSetupFailedFrames()))
	sandbox := interpreterTestSandbox(t, fake)
	// When
	result, err := sandbox.RunCode(t.Context(), "print(1)", nil)
	// Then
	sdkErr := requireKernelSetupError(t, err)
	require.Contains(t, sdkErr.Message, interpreterInstallFailure)
	require.Contains(t, sdkErr.Message, "Preparing the python kernel")
	require.Contains(t, sdkErr.Message, "process p")
	require.NotNil(t, result)
	require.Equal(t, "p", result.Process.ProcessID)
	require.Equal(t, ProcessReasonFailed, result.Process.Reason)
}

func TestRunCode_whenKernelSetupFailedWithoutDiagnostic(t *testing.T) {
	// Given
	fake := newFakeServer(t, interpreterSetupFailedRoutes(t, []any{}))
	sandbox := interpreterTestSandbox(t, fake)
	// When
	_, err := sandbox.RunCode(t.Context(), "console.log(1)", &RunCodeOptions{Language: "typescript"})
	// Then
	sdkErr := requireKernelSetupError(t, err)
	require.Contains(t, sdkErr.Message, "no setup diagnostic")
}

func TestRunCode_whenKernelSetupFailedWhileStreaming(t *testing.T) {
	// Given
	routes := interpreterSetupFailedRoutes(t, nil)
	routes["GET /processes/p/logs"] = func(w http.ResponseWriter, _ *http.Request) {
		interpreterSSE(t, w, "event: log\nid: 1\ndata: "+
			`{"timestamp":1,"channel":"setup","data":"code run failed: `+interpreterInstallFailure+`\n"}`+"\n\n"+
			"event: eof\nid: 1\ndata: {}\n\n")
	}
	fake := newFakeServer(t, routes)
	sandbox := interpreterTestSandbox(t, fake)
	var stdout bytes.Buffer
	// When
	result, err := sandbox.RunCode(t.Context(), "print(1)", &RunCodeOptions{Stdout: &stdout})
	// Then
	sdkErr := requireKernelSetupError(t, err)
	require.Contains(t, sdkErr.Message, interpreterInstallFailure)
	require.Empty(t, stdout.String())
	require.NotNil(t, result)
	require.Equal(t, "p", result.Process.ProcessID)
	require.Equal(t, ProcessReasonFailed, result.Process.Reason)
}

func TestRunCode_whenKernelSetupFailedInExplicitContext(t *testing.T) {
	// Given
	fake := newFakeServer(t, interpreterSetupFailedRoutes(t, interpreterSetupFailedFrames()))
	bound, err := interpreterTestSandbox(t, fake).Interpreter().ConnectContext("ctx", "python")
	require.NoError(t, err)
	// When
	result, err := bound.Run(t.Context(), "print(1)", nil)
	// Then
	sdkErr := requireKernelSetupError(t, err)
	require.Contains(t, sdkErr.Message, interpreterInstallFailure)
	require.Equal(t, ProcessReasonFailed, result.Process.Reason)
	require.JSONEq(t,
		`{"code":"print(1)","kind":"code","language":"python","sessionId":"ctx","keepLogs":"on_exit_ttl"}`,
		string(fake.requests()[0].Body))
}

func TestRunCode_whenKernelLostReturnsPartialResult(t *testing.T) {
	// Given
	routes := interpreterRunRoutes(t)
	routes["GET /processes/p/wait"] = jsonHandler(t, map[string]any{"reason": "lost"})
	routes["GET /processes/p/logs"] = jsonHandler(t, map[string]any{
		"frames": []any{
			map[string]any{"cursor": "1", "timestamp": 1, "channel": "stdout", "data": "partial\n"},
			map[string]any{
				"cursor": "2", "timestamp": 2, "channel": "setup",
				"data": "kernel died mid-execution: connection closed\n",
			},
		},
		"nextCursor": "2", "truncatedHead": false, "eof": true,
	})
	fake := newFakeServer(t, routes)
	sandbox := interpreterTestSandbox(t, fake)
	// When
	result, err := sandbox.RunCode(t.Context(), "print(1)", nil)
	// Then
	require.NoError(t, err)
	require.Equal(t, ProcessReasonLost, result.Process.Reason)
	require.Equal(t, "partial\n", result.Logs.Stdout)
}
