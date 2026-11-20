// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// MacOSResources sizes a macOS sandbox. Zero axes use platform defaults.
// Memory and disk must convert exactly to whole MiB, at most 2147483647 MiB.
type MacOSResources struct {
	// CPU is physical cores, at least 4 and at most 2147483647 when set.
	CPU int
	// MemoryGiB is memory in GiB, at least 4 when set.
	MemoryGiB float64
	// DiskGiB is disk capacity in GiB, at least 80 when set.
	DiskGiB float64
}

// CreateMacOSOptions configures a macOS sandbox. Nil and zero fields select
// defaults. Invalid options return ErrBadRequest before any request.
type CreateMacOSOptions struct {
	// Name stamps labels["name"] and derives a stable idempotency key.
	Name string
	// Labels attaches metadata; a conflicting "name" label is rejected.
	Labels map[string]string
	// Ports requests port forwarding; nil omits it.
	Ports []PortMapping
	// LifecyclePolicy selects automatic lifetime and idle actions.
	LifecyclePolicy *LifecyclePolicyOptions
	// Region selects this create's region; blank or "local" uses the default.
	Region string
	// Wait defaults to 60 seconds; NoWait skips waiting, WaitForever is unbounded.
	// Positive durations must be whole seconds.
	Wait time.Duration
	// CreateTimeout is the server provisioning budget; nil uses its default,
	// zero is explicit, and WaitForever selects the server maximum.
	CreateTimeout *time.Duration
	// IdempotencyKey identifies a retry; empty derives a named or random key.
	IdempotencyKey string
	// Volumes attaches disk volumes; the API refuses these with Persistent true.
	Volumes []VolumeMount
	// Env sets environment variables; nil omits it and an empty map sends {}.
	Env map[string]string
	// Secrets attaches project secret names; nil omits it.
	Secrets []string
	// Resources sizes the sandbox; nil uses platform defaults.
	Resources *MacOSResources
	// Persistent retains disk across stop/start and memory across pause/resume.
	Persistent *bool
	// Reservation selects a reservation ID or "new"; empty uses a fitting reservation.
	Reservation string
	// AutoRenew applies only when Reservation is "new"; nil uses the account default.
	AutoRenew *bool
	// NetworkPolicy configures egress policy; nil leaves the policy unspecified.
	NetworkPolicy *NetworkPolicySpec
	// ClearNetworkPolicy sends explicit null. It cannot accompany NetworkPolicy.
	ClearNetworkPolicy bool
}
