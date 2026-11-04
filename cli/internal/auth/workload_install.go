// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package auth

import (
	"context"

	"github.com/daytona/clients/cli/internal/app"
	"github.com/daytona/clients/cli/internal/config"
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
	a.StaticExchangeFn = func(ctx context.Context, base, bearer, projectID, rejected string) (string, error) {
		return exchangeStaticForProjectKey(ctx, a, base, bearer, projectID, rejected)
	}
	a.CachedStaticKeyFn = func(base, bearer, projectID string) string {
		src := staticSource(base, bearer)
		key, _, _ := lookupWorkloadKey(a, func(k config.WorkloadKey) bool { return k.ProjectID == projectID && k.Source == src })
		return key
	}
}
