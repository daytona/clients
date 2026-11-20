// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestProcessRejectsInvalidInputBeforeAnyRequest(t *testing.T) {
	tests := []struct {
		name string
		call func(context.Context, *Sandbox) error
	}{
		{"argv", func(ctx context.Context, s *Sandbox) error { _, err := s.ExecArgs(ctx, nil, nil); return err }},
		{"start argv", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().StartArgs(ctx, nil, nil)
			return err
		}},
		{"wait duration", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Exec(ctx, "x", &ExecOptions{Timeout: -9})
			return err
		}},
		{"execution duration", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Exec(ctx, "x", &ExecOptions{ExecutionTimeout: NoWait})
			return err
		}},
		{"retention", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Exec(ctx, "x", &ExecOptions{KeepLogs: ProcessKeepLogsNone})
			return err
		}},
		{"stdin", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().Start(ctx, "x", &ProcessStartOptions{Stdin: "future"})
			return err
		}},
		{"unknown retention", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().Start(ctx, "x", &ProcessStartOptions{KeepLogs: "future"})
			return err
		}},
		{"list state", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().List(ctx, &ListProcessesOptions{State: "queued"})
			return err
		}},
		{"list kind", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().List(ctx, &ListProcessesOptions{Kind: "future"})
			return err
		}},
		{"kill zero tag", func(ctx context.Context, s *Sandbox) error { _, err := s.Process().KillAll(ctx, "", nil); return err }},
		{"kill unbounded", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().KillAll(ctx, "job", &KillAllProcessesOptions{Timeout: WaitForever})
			return err
		}},
		{"kill too long", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().KillAll(ctx, "job", &KillAllProcessesOptions{Timeout: 60*time.Second + 1})
			return err
		}},
		{"id", func(_ context.Context, s *Sandbox) error { _, err := s.Process().Connect(".."); return err }},
		{"foreign identity", func(_ context.Context, s *Sandbox) error {
			_, err := s.Process().ConnectIdentity(ProcessHandleJSON{SandboxID: "other", ProcessID: "p"})
			return err
		}},
		{"missing identity", func(_ context.Context, s *Sandbox) error {
			_, err := s.Process().ConnectIdentity(ProcessHandleJSON{SandboxID: "sbx"})
			return err
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given no server routes.
			sandbox, fake := processTestSandbox(t, nil)
			// When passing invalid public input.
			err := test.call(t.Context(), sandbox)
			// Then validation fails locally in the shared error hierarchy.
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestTagsAreValidatedAcrossEntryPoints(t *testing.T) {
	for _, tag := range []string{".", "..", "a b", "a/b", "ü", strings.Repeat("x", 129), "tag\n", "\rtag"} {
		t.Run(tag, func(t *testing.T) {
			// Given each process API accepts the same tag vocabulary.
			sandbox, fake := processTestSandbox(t, nil)
			// When using an unsafe tag at each boundary.
			_, startErr := sandbox.Process().Start(t.Context(), "true", &ProcessStartOptions{Tag: tag})
			_, runErr := sandbox.Exec(t.Context(), "true", &ExecOptions{Tag: tag})
			_, listErr := sandbox.Process().List(t.Context(), &ListProcessesOptions{Tag: tag})
			_, killErr := sandbox.Process().KillAll(t.Context(), tag, nil)
			_, sessionErr := sandbox.Process().CreateSession(t.Context(), &CreateSessionOptions{Tag: tag})
			// Then none reaches the network.
			for _, err := range []error{startErr, runErr, listErr, killErr, sessionErr} {
				require.ErrorIs(t, err, ErrBadRequest)
			}
			require.Empty(t, fake.requests())
		})
	}
}

func TestMalformedProcessFieldIsRejectedWhenReturned(t *testing.T) {
	for _, test := range []struct {
		name  string
		value any
	}{
		{"id", ""},
		{"kind", nil},
		{"state", nil},
		{"cwd", false},
		{"stdin", nil},
		{"keepLogs", nil},
		{"truncatedHead", nil},
		{"createdAt", "yesterday"},
		{"startedAt", 7},
		{"exitCode", 2147483648},
		{"pid", 1.5},
		{"timeoutMs", -1},
		{"terminal", map[string]any{"cols": 65536, "rows": 24}},
		{"argv", []any{nil}},
		{"system", "yes"},
		{"login", "no"},
		{"tag", true},
		{"signal", 42},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given a response that violates a field the SDK exposes.
			fixture := processFixture("p")
			fixture[test.name] = test.value
			sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{"GET /processes/p": jsonHandler(t, fixture)})
			// When fetching it.
			_, err := sandbox.Process().Get(t.Context(), "p")
			// Then do not invent a placeholder or treat a missing state as running.
			require.ErrorIs(t, err, ErrBadRequest)
		})
	}
}

func TestProcessPreservesFutureValuesWhenServerAddsEnums(t *testing.T) {
	// Given valid metadata with future enum values and optional outcome fields absent.
	fixture := processFixture("p")
	fixture["kind"], fixture["state"], fixture["stdin"], fixture["keepLogs"] = "future-kind", "future-state", "future-stdin", "future-policy"
	fixture["reason"], fixture["signal"] = "future-reason", "SIGFUTURE"
	sandbox, _ := processTestSandbox(t, map[string]http.HandlerFunc{"GET /processes/p": jsonHandler(t, fixture)})
	// When reading from a newer server.
	info, err := sandbox.Process().Get(t.Context(), "p")
	// Then closed enums normalize but raw values and absence survive.
	require.NoError(t, err)
	require.Equal(t, ProcessKindUnknown, info.Kind)
	require.Equal(t, "future-kind", info.RawKind)
	require.Equal(t, ProcessStateUnknown, info.State)
	require.Equal(t, "future-state", info.RawState)
	require.Equal(t, ProcessStdinUnknown, info.Stdin)
	require.Equal(t, "future-stdin", info.RawStdin)
	require.Equal(t, ProcessKeepLogsUnknown, info.KeepLogs)
	require.Equal(t, "future-policy", info.RawKeepLogs)
	require.Equal(t, ProcessReasonUnknown, info.Reason)
	require.Equal(t, "future-reason", info.RawReason)
	require.Equal(t, ProcessSignalUnknown, info.Signal)
	require.Equal(t, "FUTURE", info.RawSignal)
	require.Nil(t, info.ExitCode)
}
