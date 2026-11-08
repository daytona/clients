// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"

	"github.com/daytona/clients/cli/internal/app"
)

func InstallWorkload(a *app.App) {
	a.CachedWorkloadKeyFn = func(projectID string) string {
		if a.CredentialSource != "profile-oidc" {
			return ""
		}
		key, _, _ := cachedWorkloadKeyAny(a, projectID)
		return key
	}
	a.WorkloadCredentialFn = func(ctx context.Context, projectID, rejected string) (string, error) {
		return WorkloadCredential(ctx, a, projectID, rejected)
	}
}
