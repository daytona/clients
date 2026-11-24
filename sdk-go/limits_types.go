// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// GetLimitsOptions selects the organization whose limits are read.
type GetLimitsOptions struct {
	// OrganizationID selects an organization. Empty uses the API key's
	// organization; JWT authentication requires an explicit organization ID.
	OrganizationID string
}

// UsageLimit pairs a resource count limit with its current usage.
type UsageLimit struct {
	// Limit is the maximum count; nil means unlimited.
	Limit *int64
	// Usage is the current count.
	Usage int64
}

// SandboxLimits describes sandbox count limits.
type SandboxLimits struct {
	// Concurrent counts concurrently running sandboxes.
	Concurrent UsageLimit
	// Total counts all sandboxes.
	Total UsageLimit
}

// SandboxTimeoutLimits describes the organization's sandbox timeout maximums.
type SandboxTimeoutLimits struct {
	// Max is the longest timeout a sandbox may currently have.
	Max time.Duration
	// Ceiling is the highest value the organization's own cap may be set to.
	Ceiling time.Duration
}

// ImageBuildLimits describes image-build count limits.
type ImageBuildLimits struct {
	// Concurrent counts concurrent image builds.
	Concurrent UsageLimit
}

// StorageLimits describes storage resource count limits, not byte limits.
type StorageLimits struct {
	// Volumes counts volumes.
	Volumes UsageLimit
	// Images counts images.
	Images UsageLimit
	// Snapshots counts snapshots.
	Snapshots UsageLimit
}

// SandboxResourceLimits describes per-sandbox Linux resource maximums.
type SandboxResourceLimits struct {
	// MaxCores is the maximum CPU limit in cores.
	MaxCores float64
	// MaxMemoryMiB is the maximum memory limit in MiB.
	MaxMemoryMiB int64
}

// MemberLimits describes organization membership usage.
type MemberLimits struct {
	// UsageLimit contains the membership count and maximum.
	UsageLimit
	// PendingInvitations counts outstanding membership invitations.
	PendingInvitations int64
}

// OrganizationLimits describes an organization's limits and current usage.
type OrganizationLimits struct {
	// Sandboxes contains sandbox count limits.
	Sandboxes SandboxLimits
	// SandboxTimeout contains the required sandbox timeout maximums.
	SandboxTimeout SandboxTimeoutLimits
	// ImageBuilds contains image-build count limits.
	ImageBuilds ImageBuildLimits
	// Storage contains storage resource count limits.
	Storage StorageLimits
	// SecretsPerProject contains the per-project secret limit and highest usage.
	SecretsPerProject UsageLimit
	// APIKeysPerProject contains the per-project key limit and highest usage.
	APIKeysPerProject UsageLimit
	// APIKeysPerOrganization contains the organization's live key count limit.
	APIKeysPerOrganization UsageLimit
	// SandboxResources is nil when per-sandbox resource maximums are unreported.
	SandboxResources *SandboxResourceLimits
	// MacReservations counts pending, active and ending reservations; nil is
	// unreported. Renewals do not count again.
	MacReservations *UsageLimit
	// Members is nil for project-scoped callers and when unreported.
	Members *MemberLimits
}
