// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// Snapshot is a caller-owned observation of a saved sandbox's memory and disk.
type Snapshot struct {
	// ID is the stable snapshot identifier.
	ID string
	// Name is the snapshot name.
	Name string
	// Class is the normalized sandbox class.
	Class SnapshotClass
	// RawClass preserves the server class.
	RawClass string
	// Kind is the snapshot format.
	Kind SnapshotKind
	// RawKind preserves the server kind.
	RawKind string
	// AvailableIn lists regions where the snapshot is available.
	AvailableIn []string
	// Region is the snapshot's home region.
	Region string
	// Status is its normalized readiness status.
	Status SnapshotStatus
	// RawStatus preserves the server status.
	RawStatus string
	// Error is the recorded failure.
	Error string
	// CreatedAt is the creation time.
	CreatedAt time.Time
	// UpdatedAt is the last update time.
	UpdatedAt time.Time
	// Bytes is the disk size, or nil when unreported.
	Bytes *int64
	// MemoryBytes is the saved memory size, or nil when unreported.
	MemoryBytes *int64
	// SandboxID identifies the captured sandbox, when reported.
	SandboxID string
}

// SnapshotClass identifies a snapshot's operating-system class.
type SnapshotClass string

const (
	// SnapshotClassUnknown identifies a future class.
	SnapshotClassUnknown SnapshotClass = "unknown"
	// SnapshotClassLinux identifies Linux snapshots.
	SnapshotClassLinux SnapshotClass = "linux"
)

// SnapshotKind describes the captured state.
type SnapshotKind string

const (
	// SnapshotKindUnknown identifies a future kind.
	SnapshotKindUnknown SnapshotKind = "unknown"
	// SnapshotKindFull captures memory and disk.
	SnapshotKindFull SnapshotKind = "full"
)

// SnapshotStatus identifies snapshot readiness.
type SnapshotStatus string

const (
	// SnapshotStatusUnknown identifies a future status.
	SnapshotStatusUnknown SnapshotStatus = "unknown"
	// SnapshotStatusPending means capture is queued.
	SnapshotStatusPending SnapshotStatus = "pending"
	// SnapshotStatusCreating means capture is in progress.
	SnapshotStatusCreating SnapshotStatus = "creating"
	// SnapshotStatusReady means the snapshot is usable.
	SnapshotStatusReady SnapshotStatus = "ready"
	// SnapshotStatusFailed means capture failed.
	SnapshotStatusFailed SnapshotStatus = "failed"
	// SnapshotStatusError is the alternative failure status.
	SnapshotStatusError SnapshotStatus = "error"
)
