// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCreate_whenOptionIsInvalid_namesGoFields(t *testing.T) {
	negative, odd := -1.0, 1.25
	duration := -time.Second
	open := false
	domains := []string{"example.com"}
	for _, test := range []struct {
		name   string
		opts   CreateOptions
		fields []string
	}{
		{"architecture", CreateOptions{CPUArch: "x86"}, []string{"CPUArch"}},
		{"idempotency", CreateOptions{IdempotencyKey: " key"}, []string{"IdempotencyKey"}},
		{"provision budget", CreateOptions{CreateTimeout: &duration}, []string{"CreateTimeout"}},
		{"volume", CreateOptions{Volumes: []VolumeMount{{}}}, []string{"VolumeID", "MountPath"}},
		{"network clear", CreateOptions{NetworkPolicy: &NetworkPolicySpec{}, ClearNetworkPolicy: true}, []string{"ClearNetworkPolicy", "NetworkPolicy"}},
		{"CPU", CreateOptions{Resources: &Resources{CPU: -1}}, []string{"Resources.CPU"}},
		{"CPU ceiling", CreateOptions{Resources: &Resources{CPUMax: &negative}}, []string{"Resources.CPUMax"}},
		{"CPU step", CreateOptions{Resources: &Resources{CPUMax: &odd}}, []string{"Resources.CPUMax"}},
		{"memory", CreateOptions{Resources: &Resources{MemoryGiB: -1}}, []string{"Resources.MemoryGiB"}},
		{"disk", CreateOptions{Resources: &Resources{DiskGiB: -1}}, []string{"Resources.DiskGiB"}},
		{"memory ceiling", CreateOptions{Resources: &Resources{MemoryMaxGiB: &negative}}, []string{"Resources.MemoryMaxGiB"}},
		{"disk ceiling", CreateOptions{Resources: &Resources{DiskMaxGiB: &negative}}, []string{"Resources.DiskMaxGiB"}},
		{"idle duration", CreateOptions{LifecyclePolicy: &LifecyclePolicyOptions{IdleExpiry: -time.Second}}, []string{"IdleExpiry"}},
		{"idle pair", CreateOptions{LifecyclePolicy: &LifecyclePolicyOptions{IdleExpiry: time.Minute}}, []string{"IdleExpiry", "OnIdleTimeout"}},
		{"idle clear", CreateOptions{LifecyclePolicy: &LifecyclePolicyOptions{IdleExpiry: NoTimeout, OnIdleTimeout: LifecycleActionDelete}}, []string{"IdleExpiry", "OnIdleTimeout"}},
		{"network CIDRs", CreateOptions{NetworkPolicy: &NetworkPolicySpec{AllowCIDRs: []string{"10.0.0.0/8"}}}, []string{"AllowCIDRs", "BlockAll", "AllowDomains"}},
		{"network domains", CreateOptions{NetworkPolicy: &NetworkPolicySpec{BlockAll: &open, AllowDomains: &domains}}, []string{"AllowDomains", "BlockAll"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)

			// When
			_, err := client.Create(t.Context(), "", &test.opts)

			// Then: diagnostics identify the fields the Go caller can change.
			require.ErrorIs(t, err, ErrBadRequest)
			for _, name := range test.fields {
				require.ErrorContains(t, err, name)
			}
			require.Empty(t, fake.requests())
		})
	}
}

func TestCreate_whenHeaderValueIsInvalid_namesTheFailingPart(t *testing.T) {
	for _, test := range []struct{ key, message string }{
		{" key", "IdempotencyKey must have no leading or trailing whitespace"},
		{"key ", "IdempotencyKey must have no leading or trailing whitespace"},
		{"k\u00e9y", "IdempotencyKey must be printable ASCII"},
		{" k\x01y ", "IdempotencyKey must be printable ASCII"},
	} {
		t.Run(test.key, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)

			// When
			_, err := client.Create(t.Context(), "", &CreateOptions{IdempotencyKey: test.key})

			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, test.message, detail.Message)
			require.Empty(t, fake.requests())
		})
	}
}

func TestCreateMacOS_whenOptionIsInvalid_namesGoFields(t *testing.T) {
	renew := true
	for _, test := range []struct {
		name, field string
		opts        CreateMacOSOptions
	}{
		{"renewal", "AutoRenew", CreateMacOSOptions{AutoRenew: &renew}},
		{"CPU", "Resources.CPU", CreateMacOSOptions{Resources: &MacOSResources{CPU: 3}}},
		{"memory", "Resources.MemoryGiB", CreateMacOSOptions{Resources: &MacOSResources{MemoryGiB: 1}}},
		{"disk", "Resources.DiskGiB", CreateMacOSOptions{Resources: &MacOSResources{DiskGiB: 1}}},
	} {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, nil)
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.CreateMacOS(t.Context(), "macos", &test.opts)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.ErrorContains(t, err, test.field)
			require.Empty(t, fake.requests())
		})
	}
}
