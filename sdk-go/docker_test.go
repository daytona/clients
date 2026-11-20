// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func dockerVolumeSandbox(t *testing.T, fake *fakeServer, opts ...Option) *Sandbox {
	t.Helper()
	client := newTestClient(t, fake.Server, opts...)
	sandbox, err := client.newSandbox(sandboxInit{
		Info:    SandboxInfo{ID: "sbx"},
		Options: GetSandboxOptions{ToolboxURL: fake.Server.URL, AccessToken: "caller"},
	})
	require.NoError(t, err)
	return sandbox
}

func TestDockerStatus_whenKnownOrFutureEnums(t *testing.T) {
	for _, test := range []struct {
		state, owner string
		wantState    DockerState
		wantOwner    DockerOwner
	}{
		{"not_present", "none", DockerStateNotPresent, DockerOwnerNone},
		{"disabled", "entrypoint", DockerStateDisabled, DockerOwnerEntrypoint},
		{"unmanaged", "daemon", DockerStateUnmanaged, DockerOwnerDaemon},
		{"deferred", "daemon", DockerStateDeferred, DockerOwnerDaemon},
		{"starting", "daemon", DockerStateStarting, DockerOwnerDaemon},
		{"ready", "daemon", DockerStateReady, DockerOwnerDaemon},
		{"failed", "daemon", DockerStateFailed, DockerOwnerDaemon},
		{"unsupported", "none", DockerStateUnsupported, DockerOwnerNone},
		{"future", "future", DockerStateUnknown, DockerOwnerUnknown},
	} {
		t.Run(test.state, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": jsonHandler(t, map[string]string{
				"state": test.state, "owner": test.owner, "processId": "p", "reason": "explanation",
			})})
			sandbox := dockerVolumeSandbox(t, fake)
			// When
			status, err := sandbox.Docker().Status(t.Context())
			// Then
			require.NoError(t, err)
			require.Equal(t, DockerStatus{State: test.wantState, RawState: test.state, Owner: test.wantOwner, RawOwner: test.owner, ProcessID: new("p"), Reason: new("explanation")}, status)
			requests := fake.requests()
			require.Len(t, requests, 1)
			require.Empty(t, requests[0].Query)
			require.Equal(t, "Bearer caller", requests[0].Header.Get("Authorization"))
		})
	}
}

func TestDockerWait_whenReadyOrTerminal(t *testing.T) {
	for _, state := range []string{"ready", "failed", "not_present", "disabled", "unmanaged", "unsupported"} {
		t.Run(state, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": jsonHandler(t, map[string]string{
				"state": state, "owner": "daemon", "processId": "docker-process", "reason": "reason",
			})})
			sandbox := dockerVolumeSandbox(t, fake)
			// When
			status, err := sandbox.Docker().WaitUntilReady(t.Context(), nil)
			// Then
			switch state {
			case "ready":
				require.NoError(t, err)
				require.Equal(t, DockerStateReady, status.State)
			default:
				var terminal *DockerNotReadyError
				require.ErrorAs(t, err, &terminal)
				require.ErrorIs(t, err, ErrConflict)
				require.Equal(t, state, terminal.Status.RawState)
				require.Equal(t, new("docker-process"), terminal.Status.ProcessID)
				require.Equal(t, new("reason"), terminal.Status.Reason)
			}
			require.Len(t, fake.requests(), 1)
			require.Equal(t, "1000", fake.requests()[0].Query.Get("waitMs"))
		})
	}
}

func TestDockerWait_whenInvalidTimeout(t *testing.T) {
	for _, timeout := range []time.Duration{NoWait, NoTimeout, -time.Second} {
		t.Run(timeout.String(), func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			sandbox := dockerVolumeSandbox(t, fake)
			// When
			_, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: timeout})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestDockerWait_whenSDKDeadlineExpires(t *testing.T) {
	for _, state := range []string{"starting", "deferred", "future"} {
		t.Run(state, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": jsonHandler(t, map[string]string{"state": state, "owner": "daemon"})})
			sandbox := dockerVolumeSandbox(t, fake)
			// When
			_, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: 50 * time.Millisecond})
			// Then
			var timeout *DockerWaitTimeoutError
			require.ErrorAs(t, err, &timeout)
			require.ErrorIs(t, err, ErrTimeout)
			require.NotNil(t, timeout.Status)
			require.Equal(t, state, timeout.Status.RawState)
		})
	}
}

func TestDockerWait_whenFirstRequestStalls(t *testing.T) {
	// Given
	fake := newFakeServer(t, map[string]http.HandlerFunc{"GET /docker/status": func(_ http.ResponseWriter, r *http.Request) { <-r.Context().Done() }})
	sandbox := dockerVolumeSandbox(t, fake)
	// When
	_, err := sandbox.Docker().WaitUntilReady(t.Context(), &DockerWaitOptions{Timeout: time.Millisecond})
	// Then
	var timeout *DockerWaitTimeoutError
	require.ErrorAs(t, err, &timeout)
	require.Nil(t, timeout.Status)
	require.ErrorIs(t, err, context.DeadlineExceeded)
	require.Len(t, fake.requests(), 1)
}

func TestDockerPollBudget_whenDeadlineHasFractionalRemainder(t *testing.T) {
	for _, test := range []struct {
		remaining, hold, timeout time.Duration
	}{
		{1500 * time.Millisecond, time.Second, 1750 * time.Millisecond},
		{500 * time.Millisecond, 500 * time.Millisecond, 750 * time.Millisecond},
		{time.Nanosecond, time.Millisecond, 251 * time.Millisecond},
		{0, 0, 0},
	} {
		t.Run(test.remaining.String(), func(t *testing.T) {
			// Given
			now := time.Unix(0, 0)
			// When
			hold, timeout := dockerPollBudget(now.Add(test.remaining), now)
			// Then
			require.Equal(t, test.hold, hold)
			require.Equal(t, test.timeout, timeout)
		})
	}
}
