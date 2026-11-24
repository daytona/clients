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
	// The deployment's reads are not monotonic right after a create: a read
	// after one that answered "running" can still answer "assigned" (seen
	// live, also on the SDK's post-wait refresh), so re-read until a read
	// reports the started state.
	eventually(ctx, t, 30*time.Second, func() (bool, error) {
		if sb.State() == daytona.SandboxStateStarted {
			return true, nil
		}
		return false, sb.Refresh(ctx)
	})

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
