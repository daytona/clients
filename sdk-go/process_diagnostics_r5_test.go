// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestProcessOptions_whenInvalidNameGoFields(t *testing.T) {
	for _, test := range []struct {
		field string
		call  func(context.Context, *Sandbox) error
	}{
		{"ExecutionTimeout", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().Start(ctx, "true", &ProcessStartOptions{ExecutionTimeout: NoWait})
			return err
		}},
		{"Stdin", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().Start(ctx, "true", &ProcessStartOptions{Stdin: "future"})
			return err
		}},
		{"KeepLogs", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().Start(ctx, "true", &ProcessStartOptions{KeepLogs: "future"})
			return err
		}},
		{"State", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().List(ctx, &ListProcessesOptions{State: "future"})
			return err
		}},
		{"Kind", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().List(ctx, &ListProcessesOptions{Kind: "future"})
			return err
		}},
		{"Timeout", func(ctx context.Context, s *Sandbox) error {
			_, err := s.Process().KillAll(ctx, "job", &KillAllProcessesOptions{Timeout: -time.Second})
			return err
		}},
		{"Timeout", func(ctx context.Context, s *Sandbox) error {
			handle, err := s.Process().Connect("p")
			if err != nil {
				return err
			}
			_, err = handle.Wait(ctx, &WaitProcessOptions{Timeout: NoWait})
			return err
		}},
		{"Limit", func(ctx context.Context, s *Sandbox) error {
			handle, err := s.Process().Connect("p")
			if err != nil {
				return err
			}
			_, err = handle.LogPage(ctx, &ProcessLogsOptions{Limit: -1})
			return err
		}},
		{"Encoding", func(ctx context.Context, s *Sandbox) error {
			handle, err := s.Process().Connect("p")
			if err != nil {
				return err
			}
			_, err = handle.LogPage(ctx, &ProcessLogsOptions{Encoding: "future"})
			return err
		}},
		{"EscalateAfter", func(ctx context.Context, s *Sandbox) error {
			handle, err := s.Process().Connect("p")
			if err != nil {
				return err
			}
			delay := -time.Second
			return handle.Kill(ctx, &KillProcessOptions{EscalateAfter: &delay})
		}},
	} {
		t.Run(test.field, func(t *testing.T) {
			// Given invalid options without any server routes.
			sandbox, fake := processTestSandbox(t, nil)
			// When an option is rejected.
			err := test.call(t.Context(), sandbox)
			// Then the diagnostic identifies the Go field before any request.
			require.ErrorIs(t, err, ErrBadRequest)
			var sdk *Error
			require.ErrorAs(t, err, &sdk)
			require.Contains(t, sdk.Message, test.field+" ")
			require.Empty(t, fake.requests())
		})
	}
}
