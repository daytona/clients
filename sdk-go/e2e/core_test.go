// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

//go:build e2e

package e2e_test

import (
	"errors"
	"testing"
	"time"

	daytona "go.daytona.com/sdk/v2"
)

func TestGetSearchAndFindByName(t *testing.T) {
	// Given a running sandbox created outside the SDK
	client := newClient(t)
	ctx := testContext(t)
	name := uniqueName(t, "gosdk-core")
	id := rawSandbox(t, name)

	// When the SDK reads it by ID
	sb, err := client.Get(ctx, id, nil)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}

	// Then its data is parsed
	if sb.ID() != id || sb.Name() != name {
		t.Fatalf("Get returned %s/%s, want %s/%s", sb.ID(), sb.Name(), id, name)
	}
	if state := sb.State(); state != daytona.SandboxStateStarted {
		t.Fatalf("State() = %s (raw %q), want started", state, sb.Info().RawState)
	}
	if refreshErr := sb.Refresh(ctx); refreshErr != nil {
		t.Fatalf("Refresh: %v", refreshErr)
	}

	// And FindByName and a label search return it once the index has it
	eventually(ctx, t, 30*time.Second, func() (bool, error) {
		found, findErr := client.FindByName(ctx, name, nil)
		return len(found) == 1 && found[0].ID() == id, findErr
	})
	eventually(ctx, t, 30*time.Second, func() (bool, error) {
		for item, searchErr := range client.SearchAll(ctx, &daytona.SearchSandboxesOptions{
			Labels: map[string]string{labelKey: labelValue, "name": name},
		}) {
			if searchErr != nil {
				return false, searchErr
			}
			if item.ID() == id {
				return true, nil
			}
		}
		return false, nil
	})
}

func TestGetUnknownSandboxIsNotFound(t *testing.T) {
	// Given an ID no sandbox has
	client := newClient(t)

	// When the SDK reads it
	_, err := client.Get(testContext(t), "sbx_doesNotExist0000000000-us-east-ash-1", nil)

	// Then the error matches the not-found category
	if !errors.Is(err, daytona.ErrNotFound) {
		t.Fatalf("Get unknown sandbox: err = %v, want ErrNotFound", err)
	}
}
