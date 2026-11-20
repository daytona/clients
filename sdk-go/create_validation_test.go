// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"math"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestCreate_whenInvalidOptionsAreGiven(t *testing.T) {
	zero, odd, huge := 0.0, 1.25, math.MaxFloat64
	negative, fractional := -time.Second, time.Millisecond
	open := false
	domains := []string{"example.com"}
	tests := []struct {
		name  string
		image string
		opts  CreateOptions
		code  string
	}{
		{name: "blank image", image: " \x1c"},
		{name: "blank name", opts: CreateOptions{Name: "\x1f"}},
		{name: "invalid name bytes", opts: CreateOptions{Name: string([]byte{0xff})}},
		{name: "contradictory label", opts: CreateOptions{Name: "a", Labels: map[string]string{"name": "b"}}},
		{name: "empty label key", opts: CreateOptions{Labels: map[string]string{"": "v"}}},
		{name: "region padding", opts: CreateOptions{Region: " region-a"}},
		{name: "region unicode", opts: CreateOptions{Region: "región"}},
		{name: "key padding", opts: CreateOptions{IdempotencyKey: " key"}},
		{name: "key control", opts: CreateOptions{IdempotencyKey: "key\n"}},
		{name: "invalid wait sentinel", opts: CreateOptions{Wait: NoTimeout}},
		{name: "negative wait", opts: CreateOptions{Wait: -time.Second}},
		{name: "negative server budget", opts: CreateOptions{CreateTimeout: &negative}},
		{name: "fractional server budget", opts: CreateOptions{CreateTimeout: &fractional}},
		{name: "port zero", opts: CreateOptions{Ports: []PortMapping{{Port: 0}}}},
		{name: "port too high", opts: CreateOptions{Ports: []PortMapping{{Port: 65536}}}},
		{name: "port negative", opts: CreateOptions{Ports: []PortMapping{{Port: -1}}}},
		{name: "port protocol", opts: CreateOptions{Ports: []PortMapping{{Port: 80, Protocol: "TCP"}}}},
		{name: "volume id", opts: CreateOptions{Volumes: []VolumeMount{{MountPath: "/data"}}}},
		{name: "volume path", opts: CreateOptions{Volumes: []VolumeMount{{VolumeID: "vol", MountPath: "\x1c"}}}},
		{name: "empty secret", opts: CreateOptions{Secrets: []string{""}}},
		{name: "architecture", opts: CreateOptions{CPUArch: "x86"}},
		{name: "negative cpu", opts: CreateOptions{Resources: &Resources{CPU: -1}}},
		{name: "nan cpu", opts: CreateOptions{Resources: &Resources{CPU: math.NaN()}}},
		{name: "infinite cpu", opts: CreateOptions{Resources: &Resources{CPU: math.Inf(1)}}},
		{name: "zero cpu limit", opts: CreateOptions{Resources: &Resources{CPUMax: &zero}}},
		{name: "fractional cpu limit", opts: CreateOptions{Resources: &Resources{CPUMax: &odd}}},
		{name: "overflow cpu limit", opts: CreateOptions{Resources: &Resources{CPUMax: &huge}}},
		{name: "fractional memory MiB", opts: CreateOptions{Resources: &Resources{MemoryGiB: 1.0001}}},
		{name: "negative disk", opts: CreateOptions{Resources: &Resources{DiskGiB: -1}}},
		{name: "nan disk", opts: CreateOptions{Resources: &Resources{DiskGiB: math.NaN()}}},
		{name: "overflow memory", opts: CreateOptions{Resources: &Resources{MemoryGiB: float64(math.MaxInt32)/1024 + 1}}},
		{name: "zero memory limit", opts: CreateOptions{Resources: &Resources{MemoryMaxGiB: &zero}}},
		{name: "overflow disk limit", opts: CreateOptions{Resources: &Resources{DiskMaxGiB: &huge}}},
		{name: "unpaired idle duration", opts: CreateOptions{LifecyclePolicy: &LifecyclePolicyOptions{IdleExpiry: time.Minute}}},
		{name: "unpaired idle action", opts: CreateOptions{LifecyclePolicy: &LifecyclePolicyOptions{OnIdleTimeout: LifecycleActionDelete}}},
		{name: "unsupported stop", opts: CreateOptions{LifecyclePolicy: &LifecyclePolicyOptions{OnTimeout: LifecycleActionStop}}},
		{name: "unsupported pause", opts: CreateOptions{LifecyclePolicy: &LifecyclePolicyOptions{OnTimeout: LifecycleActionPause}}},
		{name: "policy plus null", opts: CreateOptions{NetworkPolicy: &NetworkPolicySpec{}, ClearNetworkPolicy: true}},
		{name: "open cidr policy", opts: CreateOptions{NetworkPolicy: &NetworkPolicySpec{AllowCIDRs: []string{"10.0.0.0/8"}}}},
		{name: "open domains policy", opts: CreateOptions{NetworkPolicy: &NetworkPolicySpec{BlockAll: &open, AllowDomains: &domains}}, code: CodeNetworkPolicyDomainsRequireBlockAll},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{})
			client := newTestClient(t, fake.Server)

			// When
			_, err := client.Create(t.Context(), test.image, &test.opts)

			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			var detail *Error
			require.ErrorAs(t, err, &detail)
			require.Equal(t, "SDK", detail.Source)
			code := test.code
			if code == "" {
				code = CodeBadRequest
			}
			require.Equal(t, code, detail.Code)
			require.Empty(t, fake.requests())
		})
	}
}

func TestCreateMacOS_whenInvalidOptionsAreGiven(t *testing.T) {
	renew, persistent := false, true
	tests := []struct {
		name  string
		image string
		opts  CreateMacOSOptions
	}{
		{name: "empty image", image: ""},
		{name: "blank image", image: "\x1f"},
		{name: "auto renew without new", image: "macos", opts: CreateMacOSOptions{AutoRenew: &renew}},
		{name: "cpu below floor", image: "macos", opts: CreateMacOSOptions{Resources: &MacOSResources{CPU: 3}}},
		{name: "cpu overflow", image: "macos", opts: CreateMacOSOptions{Resources: &MacOSResources{CPU: math.MaxInt32 + 1}}},
		{name: "memory floor", image: "macos", opts: CreateMacOSOptions{Resources: &MacOSResources{MemoryGiB: 3.5}}},
		{name: "disk floor", image: "macos", opts: CreateMacOSOptions{Resources: &MacOSResources{DiskGiB: 79.5}}},
		{name: "disk fraction", image: "macos", opts: CreateMacOSOptions{Resources: &MacOSResources{DiskGiB: 80.0001}}},
		{name: "disk overflow", image: "macos", opts: CreateMacOSOptions{Resources: &MacOSResources{DiskGiB: math.Inf(1)}}},
		{name: "stop ephemeral", image: "macos", opts: CreateMacOSOptions{LifecyclePolicy: &LifecyclePolicyOptions{OnTimeout: LifecycleActionStop}}},
		{name: "pause persistent", image: "macos", opts: CreateMacOSOptions{Persistent: &persistent, LifecyclePolicy: &LifecyclePolicyOptions{OnTimeout: LifecycleActionPause}}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.CreateMacOS(t.Context(), test.image, &test.opts)
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}

func TestCreateFromSnapshot_whenSourceOrPolicyIsInvalid(t *testing.T) {
	for _, source := range []string{"", " ", "\x1f", "snap"} {
		t.Run(source, func(t *testing.T) {
			// Given
			fake := newFakeServer(t, map[string]http.HandlerFunc{})
			client := newTestClient(t, fake.Server)
			// When
			_, err := client.CreateFromSnapshot(t.Context(), source, &CreateFromSnapshotOptions{
				LifecyclePolicy: &LifecyclePolicyOptions{OnTimeout: LifecycleActionStop},
			})
			// Then
			require.ErrorIs(t, err, ErrBadRequest)
			require.Empty(t, fake.requests())
		})
	}
}
