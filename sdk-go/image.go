// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import "time"

// Image is a caller-owned observation of a reusable sandbox image.
type Image struct {
	// ID is the stable image identifier.
	ID string
	// Name is empty for an anonymous image.
	Name string
	// Anonymous reports whether the image has no permanent name.
	Anonymous bool
	// AvailableIn lists regions where the image is available.
	AvailableIn []string
	// Bytes is the stored size, or nil when unreported.
	Bytes *int64
	// Class is the normalized operating-system class.
	Class ImageClass
	// RawClass preserves the server class.
	RawClass string
	// CreatedAt is the creation time.
	CreatedAt time.Time
	// UpdatedAt is the last update time.
	UpdatedAt time.Time
	// Error is the recorded build failure.
	Error string
	// ExpiresAt is zero when expiry is unreported.
	ExpiresAt time.Time
	// LayerCount is nil when unreported.
	LayerCount *int64
	// Origin identifies how the image was created.
	Origin ImageOrigin
	// RawOrigin preserves the server origin.
	RawOrigin string
	// Region is the image's home region.
	Region string
	// Retention identifies its retention policy.
	Retention ImageRetention
	// RawRetention preserves the server retention policy.
	RawRetention string
	// Source describes its input, or nil when unreported.
	Source ImageSource
	// Status is the normalized build status.
	Status ImageStatus
	// RawStatus preserves the server status.
	RawStatus string
	// FormatVersion is the image format version.
	FormatVersion int64
	// FormatOutdated reports whether rebuilding is required.
	FormatOutdated bool
	// Platform reports whether this is a platform-provided image.
	Platform bool
	// Default reports whether this is the platform default.
	Default bool
	// Deprecated reports whether the image is deprecated.
	Deprecated bool
	// DiskGiB is the disk size in GiB, or nil when unreported.
	DiskGiB *float64
}

// ImageStatus is an image's build status; future values map to ImageStatusUnknown.
type ImageStatus string

const (
	// ImageStatusUnknown identifies a future server status.
	ImageStatusUnknown ImageStatus = "unknown"
	// ImageStatusPending means the build is queued.
	ImageStatusPending ImageStatus = "pending"
	// ImageStatusBuilding means the build is running.
	ImageStatusBuilding ImageStatus = "building"
	// ImageStatusReady means the image can create sandboxes.
	ImageStatusReady ImageStatus = "ready"
	// ImageStatusFailed means the build failed.
	ImageStatusFailed ImageStatus = "failed"
	// ImageStatusError is the server's alternative failure status.
	ImageStatusError ImageStatus = "error"
)

// ImageClass identifies an image's operating-system class.
type ImageClass string

const (
	// ImageClassUnknown identifies a future class.
	ImageClassUnknown ImageClass = "unknown"
	// ImageClassLinux identifies Linux images.
	ImageClassLinux ImageClass = "linux"
	// ImageClassMacOS identifies macOS images.
	ImageClassMacOS ImageClass = "macos"
)

// ImageOrigin identifies how an image was created.
type ImageOrigin string

const (
	// ImageOriginUnknown identifies a future origin.
	ImageOriginUnknown ImageOrigin = "unknown"
	// ImageOriginRegistry identifies a registry import.
	ImageOriginRegistry ImageOrigin = "registry"
	// ImageOriginDockerfile identifies a Dockerfile build.
	ImageOriginDockerfile ImageOrigin = "dockerfile"
	// ImageOriginCapture identifies a sandbox filesystem capture.
	ImageOriginCapture ImageOrigin = "capture"
	// ImageOriginWindows identifies a Windows image.
	ImageOriginWindows ImageOrigin = "windows"
	// ImageOriginMacOS identifies a macOS image.
	ImageOriginMacOS ImageOrigin = "macos"
)

// ImageRetention identifies an image's retention policy.
type ImageRetention string

const (
	// ImageRetentionUnknown identifies a future retention policy.
	ImageRetentionUnknown ImageRetention = "unknown"
	// ImageRetentionPermanent retains an image until deleted.
	ImageRetentionPermanent ImageRetention = "permanent"
	// ImageRetentionIdle removes an image after its idle window.
	ImageRetentionIdle ImageRetention = "idle"
)
