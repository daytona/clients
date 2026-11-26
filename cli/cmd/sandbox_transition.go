// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"errors"
	"time"

	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
)

// retrySandboxTransition repeats only a POST the server explicitly refused
// before creating anything. The 30-second window admits retries; an admitted
// POST keeps the API request timeout and caller context, matching the SDKs.
// Readiness waits begin after acceptance.
func retrySandboxTransition(ctx context.Context, post func() error) error {
	deadline := time.Now().Add(30 * time.Second)
	for {
		if err := cerr.FromContext(ctx); err != nil {
			return err
		}
		err := post()
		var refusal *api.APIError
		if !errors.As(err, &refusal) || refusal.Code != "SANDBOX_TRANSITION_IN_PROGRESS" {
			return err
		}
		seconds, _ := refusal.Body["retry_after_seconds"].(float64)
		delay := time.Duration(min(max(seconds, 1), 5) * float64(time.Second))
		// A retry the window cannot admit after the full pause is not worth
		// waiting for: the refusal returns at once.
		if time.Until(deadline) <= delay {
			return err
		}
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return cerr.FromContext(ctx)
		case <-timer.C:
		}
	}
}
