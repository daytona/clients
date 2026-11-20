// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

// SandboxHandleJSON is a credential- and URL-free camelCase metadata snapshot,
// shared with the other SDKs. Nil optional fields are omitted, including unknown
// capacity on a create handle. Memory and disk are GiB; CPU is cores.
// This is not reconnectable data: resume with client.Get(ctx, json.SandboxID, nil).
type SandboxHandleJSON struct {
	// SandboxID is the stable sandbox identity.
	SandboxID string `json:"sandboxId"`
	// Name is the last reported name, including a reported empty name.
	Name *string `json:"name,omitempty"`
	// State is the normalized state, omitted until known.
	State SandboxState `json:"state,omitempty"`
	// RawState is the server's status verbatim.
	RawState string `json:"rawState,omitempty"`
	// Image identifies the boot image.
	Image *SandboxArtifactJSON `json:"image,omitempty"`
	// Snapshot identifies the resumed snapshot.
	Snapshot *SandboxArtifactJSON `json:"snapshot,omitempty"`
	// Region is the explicit or inferred region.
	Region string `json:"region,omitempty"`
	// Labels is omitted until observed; a reported empty map writes {}.
	Labels *map[string]string `json:"labels,omitempty"`
	// CPU is reserved CPU in cores.
	CPU *float64 `json:"cpu,omitempty"`
	// CPUMax is the CPU limit in cores.
	CPUMax *float64 `json:"cpuMax,omitempty"`
	// Memory is reserved memory in GiB.
	Memory *float64 `json:"memory,omitempty"`
	// MemoryMax is the memory limit in GiB.
	MemoryMax *float64 `json:"memoryMax,omitempty"`
	// Disk is writable capacity in GiB.
	Disk *float64 `json:"disk,omitempty"`
	// DiskMax is the disk limit in GiB.
	DiskMax *float64 `json:"diskMax,omitempty"`
	// SandboxClass is the normalized operating-system class.
	SandboxClass SandboxClass `json:"sandboxClass,omitempty"`
	// RawSandboxClass is the server's class verbatim.
	RawSandboxClass string `json:"rawSandboxClass,omitempty"`
	// Persistent reports disk persistence.
	Persistent *bool `json:"persistent,omitempty"`
	// Spot reports interruptible capacity.
	Spot *bool `json:"spot,omitempty"`
	// SpotEvictedAt is the server's RFC 3339 timestamp verbatim.
	SpotEvictedAt string `json:"spotEvictedAt,omitempty"`
	// LifecyclePolicy is the observed automatic expiry policy.
	LifecyclePolicy *SandboxLifecycleJSON `json:"lifecyclePolicy,omitempty"`
	// ForkedFrom identifies the source sandbox.
	ForkedFrom *string `json:"forkedFrom,omitempty"`
	// ReservationID identifies the associated Mac reservation.
	ReservationID *string `json:"reservationId,omitempty"`
	// User is the reported operating-system user.
	User *string `json:"user,omitempty"`
	// Error is the reported failure explanation.
	Error *string `json:"error,omitempty"`
	// ImageBuildID identifies the associated image build.
	ImageBuildID *string `json:"imageBuildId,omitempty"`
}

// SandboxArtifactJSON identifies an image or snapshot in SandboxHandleJSON.
type SandboxArtifactJSON struct {
	// ID is the artifact identity.
	ID string `json:"id"`
	// Name is omitted unless reported.
	Name *string `json:"name,omitempty"`
}

// SandboxLifecycleJSON is the shared camelCase lifecycle snapshot. Durations
// are seconds, not nanoseconds. Timestamp strings preserve server formatting.
type SandboxLifecycleJSON struct {
	// TimeoutSeconds is the lifetime budget; zero disables expiry.
	TimeoutSeconds *int64 `json:"timeoutSeconds,omitempty"`
	// OnTimeout is the open server-defined lifetime action.
	OnTimeout *string `json:"onTimeout,omitempty"`
	// TimeoutAt is the effective lifetime deadline, in RFC 3339.
	TimeoutAt string `json:"timeoutAt,omitempty"`
	// IdleExpirySeconds is the idle budget; zero disables expiry.
	IdleExpirySeconds *int64 `json:"idleExpirySeconds,omitempty"`
	// OnIdleTimeout is the open server-defined idle action.
	OnIdleTimeout *string `json:"onIdleTimeout,omitempty"`
	// IdleTimeoutAt is the effective idle deadline, in RFC 3339.
	IdleTimeoutAt string `json:"idleTimeoutAt,omitempty"`
	// LastActivityAt is the activity observation timestamp, in RFC 3339.
	LastActivityAt string `json:"lastActivityAt,omitempty"`
	// LastActivitySource is the open server-defined activity source.
	LastActivitySource *string `json:"lastActivitySource,omitempty"`
}
