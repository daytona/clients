// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// CreateOptions configures a Linux sandbox. Zero fields use platform defaults.
// Invalid options return ErrBadRequest before any request.
type CreateOptions struct {
	// Name stamps labels["name"] and derives a stable idempotency key; empty omits it.
	Name string
	// Labels attaches metadata; a conflicting "name" label is rejected.
	Labels map[string]string
	// Ports requests port forwarding; nil omits it and an empty slice sends [].
	Ports []PortMapping
	// LifecyclePolicy selects automatic lifetime and idle actions.
	LifecyclePolicy *LifecyclePolicyOptions
	// Region selects this create's region; blank or "local" uses the default.
	Region string
	// Wait defaults to 60 seconds; NoWait returns at acceptance, WaitForever is unbounded.
	// Positive durations must be whole seconds, matching the server hold.
	Wait time.Duration
	// CreateTimeout is the server provisioning budget; nil uses its default,
	// zero is sent explicitly, and WaitForever selects the server maximum.
	CreateTimeout *time.Duration
	// IdempotencyKey identifies a retry with identical options. Empty derives
	// a key from Name or generates a random key. Keys are sent in body and header.
	IdempotencyKey string
	// Volumes attaches volumes. Mount-path restrictions are enforced by the API.
	Volumes []VolumeMount
	// Env sets environment variables; nil omits it and an empty map sends {}.
	Env map[string]string
	// Resources requests CPU cores and GiB capacity; zero axes use defaults.
	Resources *Resources
	// CPUArch selects "amd64" or "arm64"; empty uses the platform default.
	CPUArch CPUArch
	// CPUType is an optional platform CPU selector.
	CPUType string
	// Secrets lists project secret names; nil omits it and an empty slice sends [].
	Secrets []string
	// Spot selects interruptible capacity; nil uses the platform default.
	Spot *bool
	// User selects the default operating-system user.
	User string
	// NetworkPolicy configures egress policy; nil leaves the policy unspecified.
	NetworkPolicy *NetworkPolicySpec
	// ClearNetworkPolicy sends explicit null. It cannot accompany NetworkPolicy.
	ClearNetworkPolicy bool
}

// CreateFromSnapshotOptions configures a Linux sandbox restored from a snapshot.
// Resource sizing, environment, volumes and secrets come from the snapshot.
type CreateFromSnapshotOptions struct {
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
}

// PortMapping requests forwarding of one sandbox port.
type PortMapping struct {
	// Port is in the range 1–65535.
	Port int
	// Protocol is "tcp" (the default) or "udp".
	Protocol string
}

// VolumeMount attaches a project volume to a sandbox.
type VolumeMount struct {
	// VolumeID is a non-blank volume identifier.
	VolumeID string
	// MountPath is a non-blank absolute path. The API rejects root, protected
	// system paths, control characters and paths containing a ".." component.
	MountPath string
	// Subpath selects a subtree within the volume; empty omits it.
	Subpath string
	// ReadOnly requests a read-only mount, supported only by bucket volumes.
	ReadOnly bool
}
