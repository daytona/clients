// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// SandboxInfo is a caller-owned snapshot of a handle's last observation.
// Nil optional fields mean not reported. Labels are nil before a detail read.
type SandboxInfo struct {
	// ID is the stable sandbox ID.
	ID string
	// Name is the last observed name.
	Name string
	// State is the normalized lifecycle state.
	State SandboxState
	// RawState is the server's lifecycle status verbatim.
	RawState string
	// Class is the normalized operating-system class.
	Class SandboxClass
	// RawClass is the server's class verbatim.
	RawClass string
	// Error is the server's failure explanation.
	Error string
	// Image identifies the boot image, if reported.
	Image *ImageRef
	// Snapshot identifies the resumed memory snapshot, if reported.
	Snapshot *SnapshotRef
	// Labels is an independent map; nil means not yet observed.
	Labels map[string]string
	// Resources contains reserved capacity and reported maxima.
	Resources Resources
	// User is the default operating-system user.
	User string
	// Region is the explicit region, or the tag inferred from ID.
	Region string
	// MacBootMode is an open server-defined macOS boot mode.
	MacBootMode MacBootMode
	// Persistent reports disk persistence; nil means not reported.
	Persistent *bool
	// Spot reports interruptible capacity; nil means not reported.
	Spot *bool
	// SpotEvictedAt is zero until an eviction is reported.
	SpotEvictedAt time.Time
	// LifecyclePolicy is the observed automatic expiry policy.
	LifecyclePolicy *LifecyclePolicy
	// ForkedFrom identifies the source sandbox of a fork.
	ForkedFrom string
	// ReservationID identifies the associated Mac reservation.
	ReservationID string
	// ImageBuildID identifies the associated image build.
	ImageBuildID string
	// CreatedAt is the creation timestamp, or zero when absent.
	CreatedAt time.Time
	// UpdatedAt is the update timestamp, or zero when absent.
	UpdatedAt time.Time
	// StartedAt is the last start timestamp, or zero when absent.
	StartedAt time.Time
	// StoppedAt is the last stop timestamp, or zero when absent.
	StoppedAt time.Time
	// LastActivityAt is the last activity timestamp, or zero when absent.
	LastActivityAt time.Time
	// CreateTimeoutAt is the create deadline, or zero when absent.
	CreateTimeoutAt time.Time
	// PortMap contains observed port assignments.
	PortMap []PortAssignment
}

// ImageRef identifies the image a sandbox booted.
type ImageRef struct {
	// ID identifies the image.
	ID string
	// Name is the optional image name.
	Name string
}

// SnapshotRef identifies the memory snapshot a sandbox resumed.
type SnapshotRef struct {
	// ID identifies the snapshot.
	ID string
	// Name is the optional snapshot name.
	Name string
}

// Resources describes sandbox capacity. CPU and CPUMax are cores; memory and
// disk fields are GiB. Optional maxima are nil when unreported.
type Resources struct {
	// CPU is reserved CPU in cores.
	CPU float64
	// CPUMax is the CPU limit in cores.
	CPUMax *float64
	// MemoryGiB is reserved memory in GiB.
	MemoryGiB float64
	// MemoryMaxGiB is the memory limit in GiB.
	MemoryMaxGiB *float64
	// DiskGiB is writable disk capacity in GiB.
	DiskGiB float64
	// DiskMaxGiB is the disk limit in GiB.
	DiskMaxGiB *float64
}

// PortAssignment is an observed sandbox port assignment.
type PortAssignment struct {
	// HostPort is the host-side port.
	HostPort int
	// VMPort is the sandbox-side port.
	VMPort int
	// Protocol is the open server protocol name.
	Protocol string
}

// LifecyclePolicy is the observed timeout/idle policy. Nil durations mean not
// reported; a reported zero disables that expiry. Actions are open strings.
type LifecyclePolicy struct {
	// Timeout is the lifetime budget; nil is absent and zero disables expiry.
	Timeout *time.Duration
	// OnTimeout is the action taken at the lifetime deadline.
	OnTimeout LifecycleAction
	// TimeoutAt is the effective lifetime deadline.
	TimeoutAt time.Time
	// IdleExpiry is the idle budget; nil is absent and zero disables expiry.
	IdleExpiry *time.Duration
	// OnIdleTimeout is the action taken at the idle deadline.
	OnIdleTimeout LifecycleAction
	// IdleTimeoutAt is the effective idle deadline.
	IdleTimeoutAt time.Time
	// LastActivityAt is the activity observation timestamp.
	LastActivityAt time.Time
	// LastActivitySource is the open server activity source.
	LastActivitySource string
}

// LifecycleAction is an open server-defined lifecycle action.
type LifecycleAction string

const (
	// LifecycleActionStop stops the sandbox.
	LifecycleActionStop LifecycleAction = "stop"
	// LifecycleActionPause pauses the sandbox.
	LifecycleActionPause LifecycleAction = "pause"
	// LifecycleActionDelete deletes the sandbox.
	LifecycleActionDelete LifecycleAction = "delete"
)

// MacBootMode is an open server-defined macOS boot mode.
type MacBootMode string

// SandboxClass identifies the sandbox operating-system class.
type SandboxClass string

const (
	// SandboxClassUnknown is an unrecognized server class; see RawClass.
	SandboxClassUnknown SandboxClass = "unknown"
	// SandboxClassLinux identifies Linux sandboxes.
	SandboxClassLinux SandboxClass = "linux"
	// SandboxClassMacOS identifies macOS sandboxes.
	SandboxClassMacOS SandboxClass = "macos"
)
