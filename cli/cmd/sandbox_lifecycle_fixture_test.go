// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import gen "github.com/daytona/clients/cli/gen/api"

func sandboxLifecycleFixture(id string, idleSeconds int32, action gen.LifecycleAction) string {
	return fixtureJSON(gen.SandboxLifecyclePolicy{
		Id: id,
		LifecyclePolicy: gen.LifecyclePolicy{
			IdleExpirySeconds: &idleSeconds,
			OnIdleTimeout:     &action,
		},
	})
}
