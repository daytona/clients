// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"os"
	"testing"

	daytona "go.daytona.com/sdk/v2"
)

// smokeImage matches the Python and TypeScript build-base override.
func smokeImage() string {
	if image, set := os.LookupEnv("SMOKE_IMAGE"); set {
		return image
	}
	return "python:3.12-slim"
}

// newBuildClient reports unsupported build scenarios as skips, before any I/O.
func newBuildClient(t *testing.T) *daytona.Client {
	t.Helper()
	if os.Getenv("DAYTONA_SKIP_DOCKERFILE") != "" {
		t.Skip("DAYTONA_SKIP_DOCKERFILE is set")
	}
	return newClient(t)
}

// newToolboxClient leaves control-plane scenarios enabled when toolbox is absent.
func newToolboxClient(t *testing.T) *daytona.Client {
	t.Helper()
	requireToolbox(t)
	return newClient(t)
}

func requireToolbox(t *testing.T) {
	t.Helper()
	if os.Getenv("DAYTONA_SKIP_TOOLBOX") != "" {
		t.Skip("DAYTONA_SKIP_TOOLBOX is set")
	}
}
