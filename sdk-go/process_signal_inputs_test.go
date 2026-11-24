// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestKillRefusesInvalidSignalsBeforeAnyRequest(t *testing.T) {
	delay := 601 * time.Second
	cases := map[string]KillProcessOptions{
		"escalation target without a delay": {EscalateTo: ProcessSignalKill},
		"prefixed signal":                   {Signal: "SIGTERM"},
		"unknown signal":                    {Signal: "STOP"},
		"non-ASCII signal":                  {Signal: "k\u0131ll"},
		"prefixed escalation target":        {EscalateAfter: new(time.Duration), EscalateTo: "SIGKILL"},
		"delay over 600 seconds":            {EscalateAfter: &delay},
	}
	for name, options := range cases {
		t.Run(name, func(t *testing.T) {
			// Given a process handle.
			sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{})
			handle, err := sandbox.Process().Connect("p")
			require.NoError(t, err)
			// When killing it with invalid options.
			err = handle.Kill(t.Context(), &options)
			// Then the SDK refuses them and sends nothing.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestKillNormalizesSignalCase(t *testing.T) {
	// Given a process handle.
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{
		"POST /processes/{id}/signals": func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) },
	})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	delay := 600 * time.Second
	// When killing with lower-case names and the longest delay.
	require.NoError(t, handle.Kill(t.Context(), &KillProcessOptions{Signal: "int", EscalateAfter: &delay, EscalateTo: "kill"}))
	// Then the wire gets the upper-case, prefixed names.
	require.JSONEq(t, `{"signal":"SIGINT","escalateAfterMs":600000,"escalateTo":"SIGKILL"}`, string(fake.requests()[0].Body))
}

func TestKillNamesTheEscalationTargetWhenItIsInvalid(t *testing.T) {
	// Given a process handle.
	sandbox, fake := processTestSandbox(t, map[string]http.HandlerFunc{})
	handle, err := sandbox.Process().Connect("p")
	require.NoError(t, err)
	delay := time.Second
	// When the escalation target is prefixed.
	err = handle.Kill(t.Context(), &KillProcessOptions{EscalateAfter: &delay, EscalateTo: "SIGKILL"})
	// Then the error names EscalateTo, not Signal, and nothing is sent.
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorContains(t, err, "EscalateTo must be TERM")
	require.Empty(t, fake.requests())
}

func TestCreateContextRefusesAnEmptyLanguageBeforeAnyRequest(t *testing.T) {
	// Given an interpreter whose server would accept any request.
	fake := newFakeServer(t, map[string]http.HandlerFunc{
		"POST /sessions": jsonHandler(t, map[string]any{"id": "ctx", "kernel": ""}),
	})
	service := interpreterTestSandbox(t, fake).Interpreter()
	// When creating a context with an empty language.
	result, err := service.CreateContext(t.Context(), "", nil)
	// Then the SDK refuses it and sends nothing.
	require.Nil(t, result)
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorContains(t, err, "language must be a non-empty string")
	require.Empty(t, fake.requests())
}
