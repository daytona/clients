// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCreateFromBuild_whenOptionsAreInvalid(t *testing.T) {
	negative := -time.Second
	tests := []struct {
		name string
		opts CreateFromBuildOptions
	}{
		{"name", CreateFromBuildOptions{CreateOptions: CreateOptions{Name: " "}}},
		{"labels", CreateFromBuildOptions{CreateOptions: CreateOptions{Name: "a", Labels: map[string]string{"name": "b"}}}},
		{"empty label", CreateFromBuildOptions{CreateOptions: CreateOptions{Labels: map[string]string{"": "v"}}}},
		{"region", CreateFromBuildOptions{CreateOptions: CreateOptions{Region: " us"}}},
		{"idempotency", CreateFromBuildOptions{CreateOptions: CreateOptions{IdempotencyKey: "key\n"}}},
		{"wait", CreateFromBuildOptions{CreateOptions: CreateOptions{Wait: NoTimeout}}},
		{"fractional wait", CreateFromBuildOptions{CreateOptions: CreateOptions{Wait: time.Millisecond}}},
		{"create timeout", CreateFromBuildOptions{CreateOptions: CreateOptions{CreateTimeout: &negative}}},
		{"ports", CreateFromBuildOptions{CreateOptions: CreateOptions{Ports: []PortMapping{{Port: 0}}}}},
		{"lifecycle", CreateFromBuildOptions{CreateOptions: CreateOptions{
			LifecyclePolicy: &LifecyclePolicyOptions{IdleExpiry: time.Minute},
		}}},
		{"unsupported lifecycle", CreateFromBuildOptions{CreateOptions: CreateOptions{
			LifecyclePolicy: &LifecyclePolicyOptions{OnTimeout: LifecycleActionStop},
		}}},
		{"architecture", CreateFromBuildOptions{CreateOptions: CreateOptions{CPUArch: "other"}}},
		{"volumes", CreateFromBuildOptions{CreateOptions: CreateOptions{Volumes: []VolumeMount{{VolumeID: "vol"}}}}},
		{"secrets", CreateFromBuildOptions{CreateOptions: CreateOptions{Secrets: []string{""}}}},
		{"network policy", CreateFromBuildOptions{CreateOptions: CreateOptions{
			NetworkPolicy: &NetworkPolicySpec{}, ClearNetworkPolicy: true,
		}}},
		{"resources", CreateFromBuildOptions{CreateOptions: CreateOptions{Resources: &Resources{CPU: -1}}}},
		{"memory", CreateFromBuildOptions{CreateOptions: CreateOptions{Resources: &Resources{MemoryGiB: 1.0001}}}},
		{"negative retention", CreateFromBuildOptions{IdleExpiry: -time.Second}},
		{"fractional retention", CreateFromBuildOptions{IdleExpiry: time.Millisecond}},
		{"negative build budget", CreateFromBuildOptions{BuildTimeout: -time.Second}},
		{"invalid build sentinel", CreateFromBuildOptions{BuildTimeout: NoWait}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given invalid create or build options.
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)

			// When composing a build and create.
			sandbox, err := client.CreateFromBuild(t.Context(), NewImageBuilder("alpine"), &test.opts)

			// Then invalid input cannot trigger a billed request.
			require.Nil(t, sandbox)
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, "SDK", detail.Source)
			require.Empty(t, fake.requests())
		})
	}
}

func TestCreateFromBuild_whenBuilderIsNil(t *testing.T) {
	// Given no builder.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)

	// When creating from a nil builder.
	_, err := client.CreateFromBuild(t.Context(), nil, nil)

	// Then validation fails before any request.
	require.ErrorIs(t, err, ErrBadRequest)
	require.Empty(t, fake.requests())
}

func TestCreateFromBuild_whenCreateAndBuildOptionsAreInvalid(t *testing.T) {
	// Given both an invalid create option and an invalid build option.
	fake := newFakeServer(t, nil)
	client := newTestClient(t, fake.Server)

	// When validation runs.
	_, err := client.CreateFromBuild(t.Context(), NewImageBuilder("alpine"), &CreateFromBuildOptions{
		CreateOptions: CreateOptions{Name: " "}, IdleExpiry: -time.Second,
	})

	// Then create validation takes precedence over build validation.
	require.ErrorIs(t, err, ErrBadRequest)
	require.ErrorContains(t, err, "name")
	require.Empty(t, fake.requests())
}
