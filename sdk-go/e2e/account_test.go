// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	daytona "go.daytona.com/sdk/v2"
)

func TestAccountVolumes(t *testing.T) {
	// Given: a unique name; volumes have no labels field.
	client, ctx := newClient(t), testContext(t)
	name := uniqueName(t, "gosdk-e2e-account-volume")
	t.Cleanup(func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()
		if err := client.Volumes().Delete(cleanupCtx, name, nil); err != nil && !errors.Is(err, daytona.ErrNotFound) {
			t.Errorf("clean up account volume: %v", err)
		}
	})
	// When
	created, err := client.Volumes().Create(ctx, name, &daytona.CreateVolumeOptions{SizeGiB: 1})
	// Then
	require.NoError(t, err)
	require.NotEmpty(t, created.ID)
	require.Equal(t, daytona.VolumeTypeDisk, created.Type)

	t.Run("get by name", func(t *testing.T) {
		// When
		got, getErr := client.Volumes().Get(ctx, name, nil)
		// Then
		require.NoError(t, getErr)
		require.Equal(t, created.ID, got.ID)
	})
	t.Run("get by id", func(t *testing.T) {
		// When
		got, getErr := client.Volumes().Get(ctx, created.ID, &daytona.GetVolumeOptions{IDOnly: true})
		// Then
		require.NoError(t, getErr)
		require.Equal(t, name, got.Name)
	})
	t.Run("list all", func(t *testing.T) {
		// When
		var found bool
		for volume, listErr := range client.Volumes().ListAll(ctx, &daytona.ListVolumesOptions{Limit: 100}) {
			require.NoError(t, listErr)
			if volume.ID == created.ID {
				found = true
				break
			}
		}
		// Then
		require.True(t, found)
	})
	t.Run("missing", func(t *testing.T) {
		// When
		_, getErr := client.Volumes().Get(ctx, name+"-absent", nil)
		// Then
		require.ErrorIs(t, getErr, daytona.ErrNotFound)
	})
}

func TestAccountSecrets(t *testing.T) {
	// Given: a unique name; secrets have no labels field.
	client, ctx := newClient(t), testContext(t)
	name := strings.ToUpper(strings.ReplaceAll(uniqueName(t, "GOSDK_E2E_ACCOUNT_SECRET"), "-", "_"))
	value, description := "disposable-account-e2e-value", "gosdk-e2e"
	t.Cleanup(func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()
		if err := client.Secrets().Delete(cleanupCtx, name); err != nil && !errors.Is(err, daytona.ErrNotFound) {
			t.Errorf("clean up account secret: %v", err)
		}
	})
	// When
	created, err := client.Secrets().Set(ctx, name, value, &daytona.SetSecretOptions{
		AllowedDomains: []string{"api.example.com"}, Description: &description, IfNotExists: true,
	})
	// Then
	require.NoError(t, err)
	require.Equal(t, name, created.Name)
	require.NotContains(t, fmt.Sprintf("%v %#v", created, created), value)

	t.Run("get", func(t *testing.T) {
		// When
		got, getErr := client.Secrets().Get(ctx, name)
		// Then
		require.NoError(t, getErr)
		require.Equal(t, []string{"api.example.com"}, got.AllowedDomains)
		require.Equal(t, &description, got.Description)
	})
	t.Run("list all", func(t *testing.T) {
		// When
		var found bool
		for secret, listErr := range client.Secrets().ListAll(ctx, &daytona.ListSecretsOptions{Limit: 100}) {
			require.NoError(t, listErr)
			if secret.Name == name {
				found = true
				break
			}
		}
		// Then
		require.True(t, found)
	})
	t.Run("guard", func(t *testing.T) {
		// When
		_, setErr := client.Secrets().Set(ctx, name, value, &daytona.SetSecretOptions{
			AllowedDomains: []string{"api.example.com"}, IfNotExists: true,
		})
		// Then
		require.ErrorIs(t, setErr, daytona.ErrSecretAlreadyExists)
	})
	t.Run("replace preserves description", func(t *testing.T) {
		// When
		got, setErr := client.Secrets().Set(ctx, name, value+"-replacement", &daytona.SetSecretOptions{
			AllowedDomains: []string{"api.example.com"},
		})
		// Then
		require.NoError(t, setErr)
		require.Equal(t, &description, got.Description)
	})
}

func TestAccountReads(t *testing.T) {
	client, ctx := newClient(t), testContext(t)
	t.Run("current api key", func(t *testing.T) {
		// When
		key, err := client.CurrentAPIKey(ctx)
		// Then
		require.NoError(t, err)
		require.NotEmpty(t, key.ID)
		require.NotEmpty(t, key.ProjectID)
		require.NotEmpty(t, key.Context.OrgID)
	})
	t.Run("limits", func(t *testing.T) {
		// When
		limits, err := client.Limits(ctx, nil)
		// Then
		require.NoError(t, err)
		require.GreaterOrEqual(t, limits.Sandboxes.Total.Usage, int64(0))
	})
	t.Run("budget", func(t *testing.T) {
		// Given: the budget endpoint requires this independently granted scope.
		key, keyErr := client.CurrentAPIKey(ctx)
		require.NoError(t, keyErr)
		if !slices.Contains(key.ProjectPermissions, "read:spending") {
			t.Skip("API key lacks read:spending; budget live check requires that scope")
		}
		// When
		budget, err := client.ProjectBudget(ctx)
		// Then
		require.NoError(t, err)
		if budget.SpentCents != nil {
			require.GreaterOrEqual(t, *budget.SpentCents, int64(0))
		}
	})
	t.Run("audit actions", func(t *testing.T) {
		// When
		actions, err := client.ListAuditActions(ctx)
		// Then
		require.NoError(t, err)
		require.NotEmpty(t, actions.Targets)
	})
}

func TestAccountMacReadAccess(t *testing.T) {
	client, ctx := newClient(t), testContext(t)
	t.Run("reservations", func(t *testing.T) {
		// When: read only, never purchase a billed Mac.
		_, err := client.Reservations().List(ctx, nil)
		// Then
		if errors.Is(err, daytona.ErrForbidden) || errors.Is(err, daytona.ErrNotSupported) {
			t.Skip("project has no access to Mac reservations")
		}
		require.NoError(t, err)
	})
	t.Run("models", func(t *testing.T) {
		// When
		_, err := client.MacModels().List(ctx)
		// Then
		if errors.Is(err, daytona.ErrForbidden) || errors.Is(err, daytona.ErrNotSupported) {
			t.Skip("project has no access to Mac models")
		}
		require.NoError(t, err)
	})
	t.Run("catalog", func(t *testing.T) {
		// When
		_, err := client.MacModels().Catalog(ctx, nil)
		// Then
		if errors.Is(err, daytona.ErrForbidden) || errors.Is(err, daytona.ErrNotSupported) {
			t.Skip("project has no access to Mac catalog")
		}
		require.NoError(t, err)
	})
}
