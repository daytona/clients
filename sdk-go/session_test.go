// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestSessionCreatesShellWhenConfigured(t *testing.T) {
	// Given a persistent shell response.
	fixture := processFixture("shell/one")
	fixture["kind"] = "session"
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{"POST /processes": jsonHandler(t, fixture)})
	login := true
	// When creating the shell.
	session, err := sandbox.Process().CreateSession(t.Context(), &CreateSessionOptions{
		Tag: "job", Cwd: "/app", Env: map[string]string{"CI": "1"}, Shell: "bash", Login: &login, Name: "build",
	})
	// Then shell settings are sent without stdin, command or retention overrides.
	require.NoError(t, err)
	require.JSONEq(t, `{"kind":"session","tag":"job","cwd":"/app","env":{"CI":"1"},"shell":"bash","login":true,"name":"build"}`,
		string(fake.requests()[0].Body))
	require.Equal(t, &SessionJSON{SandboxID: "sbx", SessionID: "shell/one"}, session.ToJSON())
}

func TestSessionCommandRetainsSessionWhenRun(t *testing.T) {
	// Given a connected persistent shell and an inline command response.
	fixture := processFixture("command")
	fixture["state"], fixture["exitCode"], fixture["reason"] = "terminal", 0, "exited"
	fixture["logs"] = processLogsFixture(processFrameFixture("stdout", "ok"))
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{"POST /processes": jsonHandler(t, fixture)})
	session, err := sandbox.Process().ConnectSession("shell/one")
	require.NoError(t, err)
	// When running a command with independent wait and execution deadlines.
	result, err := session.Run(t.Context(), "echo ok", &SessionRunOptions{Timeout: 2 * time.Second, ExecutionTimeout: 3 * time.Second, Name: "step"})
	// Then it stays inside that shell and uses the shared output path.
	require.NoError(t, err)
	require.Equal(t, "ok", result.Stdout)
	require.JSONEq(t, `{"kind":"exec","shellCommand":"echo ok","sessionId":"shell/one","stdin":"closed",
		"timeoutMs":3000,"name":"step","keepLogs":"on_exit_ttl"}`, string(fake.requests()[0].Body))
	require.Equal(t, "2000", fake.requests()[0].Query.Get("waitMs"))
}

func TestSessionStartsCommandWhenQueued(t *testing.T) {
	// Given a queued command inside a shell.
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{"POST /processes": jsonHandler(t, processFixture("queued"))})
	session, err := sandbox.Process().ConnectSession("shell")
	require.NoError(t, err)
	// When starting a command rather than waiting.
	handle, err := session.Start(t.Context(), "read line", &SessionStartOptions{Stdin: ProcessStdinOpen, ExecutionTimeout: 4 * time.Second})
	// Then it can accept stdin and does not use a blocking create.
	require.NoError(t, err)
	require.Equal(t, "queued", handle.ID())
	require.Empty(t, fake.requests()[0].Query)
	require.JSONEq(t, `{"kind":"exec","shellCommand":"read line","sessionId":"shell","stdin":"open",
		"timeoutMs":4000,"keepLogs":"on_exit_ttl"}`, string(fake.requests()[0].Body))
}

func TestSessionDeletionWhenConnectedByIdentity(t *testing.T) {
	// Given an identity from another worker.
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"DELETE /processes/{id}": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(204) },
	})
	session, err := sandbox.Process().ConnectSessionIdentity(SessionJSON{SandboxID: "sbx", SessionID: "shell/one"})
	require.NoError(t, err)
	// When deleting it.
	err = session.Delete(t.Context())
	// Then it deletes exactly once and does not perform a redundant read.
	require.NoError(t, err)
	require.Len(t, fake.requests(), 1)
	require.Equal(t, "/processes/shell%2Fone", fake.requests()[0].Path)
}

func TestSessionLossIsNotHealedWhenServerRefusesCommand(t *testing.T) {
	for _, code := range []string{"SESSION_NOT_FOUND", "SESSION_ENDED"} {
		t.Run(code, func(t *testing.T) {
			// Given the persistent shell has vanished.
			status, sentinel := 404, ErrSessionNotFound
			if code == "SESSION_ENDED" {
				status, sentinel = 410, ErrSessionEnded
			}
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{"POST /processes": func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(status)
				writeJSON(t, w, map[string]string{"code": code, "source": "DAYTONA_DAEMON", "message": "gone"})
			}})
			session, err := sandbox.Process().ConnectSession("gone")
			require.NoError(t, err)
			// When submitting a command that depends on prior state.
			_, err = session.Start(t.Context(), "use_state", nil)
			// Then the loss is preserved, with no recreation or retry.
			require.ErrorIs(t, err, sentinel)
			require.Len(t, fake.requests(), 1)
		})
	}
}
