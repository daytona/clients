// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestToolboxTimeoutOptions_whenInvalidNameGoField(t *testing.T) {
	for name, call := range map[string]func(context.Context, *Sandbox) error{
		"Docker": func(ctx context.Context, s *Sandbox) error {
			_, err := s.Docker().WaitUntilReady(ctx, &DockerWaitOptions{Timeout: -time.Second})
			return err
		},
		"Volumes": func(ctx context.Context, s *Sandbox) error {
			_, err := s.Volumes().Pull(ctx, "volume", "request", &PullVolumeOptions{Timeout: -time.Second})
			return err
		},
	} {
		t.Run(name, func(t *testing.T) {
			// Given invalid options and no routes.
			sandbox, fake := processTestSandbox(t, nil)
			// When validating the budget.
			err := call(t.Context(), sandbox)
			// Then the Go Timeout field is named without a request.
			require.ErrorIs(t, err, ErrBadRequest)
			var sdk *Error
			require.ErrorAs(t, err, &sdk)
			require.Contains(t, sdk.Message, "Timeout ")
			require.Empty(t, fake.requests())
		})
	}
}
