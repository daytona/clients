// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"strings"
	"time"
	"unicode/utf8"
)

// ImageCPUArch selects the CPU architecture for an image build or import.
type ImageCPUArch string

const (
	// ImageCPUArchAMD64 selects x86-64.
	ImageCPUArchAMD64 ImageCPUArch = "amd64"
	// ImageCPUArchARM64 selects ARM64.
	ImageCPUArchARM64 ImageCPUArch = "arm64"
)

// BuildImageOptions controls image naming, retention and placement.
type BuildImageOptions struct {
	// Name makes an image permanent; empty creates an anonymous image.
	Name string
	// IdleExpiry is the anonymous image's idle retention. Zero omits it,
	// WaitForever requests the server maximum; otherwise use positive whole seconds.
	// Named images cannot combine Name and IdleExpiry.
	IdleExpiry time.Duration
	// Region selects placement. Empty, blank or "local" uses the default.
	Region string
	// CPUArch selects amd64 or arm64; empty uses the server default.
	CPUArch ImageCPUArch
}

// BuildFromContextOptions selects a Dockerfile in a path or reader build context.
type BuildFromContextOptions struct {
	// BuildImageOptions controls naming, retention and placement.
	BuildImageOptions
	// Dockerfile is the relative path inside the context; empty uses Dockerfile.
	Dockerfile string
	// Timeout bounds only the build submission after upload. Zero uses the
	// client's request timeout; NoTimeout disables it. Uploads have their own
	// no-progress bound from the client's request timeout, not this total bound.
	Timeout time.Duration
}

// PullImageOptions controls a registry import.
type PullImageOptions struct {
	// BuildImageOptions controls naming, retention and placement.
	BuildImageOptions
	// Wait defaults to true when nil; false returns after submission and lookup.
	Wait *bool
	// Timeout bounds submission, lookup and readiness together. Zero or
	// WaitForever is unbounded; caller cancellation always applies.
	Timeout time.Duration
}

// BuildImageWaitOptions controls a build followed by readiness.
type BuildImageWaitOptions struct {
	// BuildImageOptions controls naming, retention and placement.
	BuildImageOptions
	// Timeout starts after submission and its initial lookup, excluding local
	// archiving and upload. Zero or WaitForever has no SDK readiness deadline.
	Timeout time.Duration
}

type imageRegistrySource struct {
	Type string `json:"type"`
	Ref  string `json:"ref"`
}

type imageBuildRequest struct {
	Name            string               `json:"name,omitempty"`
	IdleExpiry      *int64               `json:"idle_expiry_seconds,omitempty"`
	Region          string               `json:"region_id,omitempty"`
	CPUArch         ImageCPUArch         `json:"cpu_arch,omitempty"`
	Source          *imageRegistrySource `json:"source,omitempty"`
	Dockerfile      string               `json:"dockerfile,omitempty"`
	ContextUploadID string               `json:"context_upload_id,omitempty"`
}

func imageOptions(options BuildImageOptions) (imageBuildRequest, error) {
	result := imageBuildRequest{Name: options.Name, Region: options.Region, CPUArch: options.CPUArch}
	if options.Name != "" && (createTrim(options.Name) == "" || !utf8.ValidString(options.Name)) {
		return result, badRequest("Name must be a non-empty UTF-8 string")
	}
	region := createTrim(options.Region)
	if region == "" || strings.EqualFold(region, "local") {
		result.Region = ""
	} else if err := headerText(options.Region, "Region"); err != nil {
		return result, err
	}
	switch options.CPUArch {
	case "", ImageCPUArchAMD64, ImageCPUArchARM64:
	default:
		return result, badRequest("CPUArch must be amd64 or arm64")
	}
	switch {
	case options.IdleExpiry == 0:
	case options.IdleExpiry == WaitForever:
		maximum := int64(-1)
		result.IdleExpiry = &maximum
	case options.IdleExpiry < 0 || options.IdleExpiry%time.Second != 0:
		return result, badRequest("IdleExpiry must be positive whole seconds or WaitForever")
	default:
		seconds := wireSeconds(options.IdleExpiry)
		result.IdleExpiry = &seconds
	}
	if result.Name != "" && result.IdleExpiry != nil {
		return result, badRequest("IdleExpiry applies only to anonymous images; omit Name or IdleExpiry")
	}
	return result, nil
}
