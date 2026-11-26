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
// before creating anything. Readiness waits begin after this acceptance budget.
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
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return err
		}
		seconds, _ := refusal.Body["retry_after_seconds"].(float64)
		delay := time.Duration(min(max(seconds, 1), 5) * float64(time.Second))
		timer := time.NewTimer(min(delay, remaining))
		select {
		case <-ctx.Done():
			timer.Stop()
			return cerr.FromContext(ctx)
		case <-timer.C:
		}
		if !time.Now().Before(deadline) {
			return err
		}
	}
}
