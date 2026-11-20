// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"time"
)

// CreateFromBuildOptions configures an anonymous image build followed by a
// Linux sandbox create. Invalid options return ErrBadRequest before any request.
type CreateFromBuildOptions struct {
	// CreateOptions controls the sandbox; Region and CPUArch also apply to the build.
	CreateOptions
	// IdleExpiry sets anonymous image retention, as in BuildImageOptions:
	// zero omits it, WaitForever selects the server maximum, otherwise use
	// positive whole seconds. The sandbox Name does not name the image.
	IdleExpiry time.Duration
	// BuildTimeout bounds image readiness after build submission and its initial
	// lookup, excluding local archiving and upload. Zero or WaitForever has no
	// SDK deadline. The sandbox receives the full CreateOptions.Wait afterward.
	BuildTimeout time.Duration
}

// CreateFromBuild builds an anonymous image, waits until ready, then creates a
// Linux sandbox from its ID. All create options and the name-derived idempotency
// key are prepared before build options are validated or any billed request is
// sent. A nil builder or invalid option returns ErrBadRequest without a request.
// Region and CPUArch select both build and sandbox placement.
//
// BuildTimeout has its own readiness budget; it never consumes the sandbox's
// Wait budget. A failed build returns ImageBuildFailedError, including its image
// ID; an exhausted build budget returns ErrRequestTimeout. Neither creates a
// sandbox. Caller context cancellation applies to both stages.
//
// After image readiness, errors and returned handles behave exactly as Create:
// an accepted create returns its last observed handle even when its wait fails.
// Keep or delete every non-nil handle, including on caller cancellation; a lost
// create response carries the idempotency key in its uncertain-request error.
// The image is not deleted automatically; a returned sandbox's Info().Image
// identifies it. Delete sandboxes before deleting their images.
func (c *Client) CreateFromBuild(
	ctx context.Context, builder *ImageBuilder, opts *CreateFromBuildOptions,
) (*Sandbox, error) {
	if err := c.ensureOpen(); err != nil {
		return nil, err
	}
	if builder == nil {
		return nil, badRequest("builder must not be nil")
	}
	options := CreateFromBuildOptions{}
	if opts != nil {
		options = *opts
	}
	prepared, err := prepareLinuxCreate("img_pending", options.CreateOptions)
	if err != nil {
		return nil, err
	}
	prepared.body.Key = createIdempotencyKey(prepared.body.Name, prepared.body.Key)
	buildOptions := BuildImageWaitOptions{
		BuildImageOptions: BuildImageOptions{
			Region: prepared.body.Region, CPUArch: options.CPUArch, IdleExpiry: options.IdleExpiry,
		},
		Timeout: options.BuildTimeout,
	}
	image, err := c.Images().BuildAndWait(ctx, builder, &buildOptions)
	if err != nil {
		return nil, err
	}
	prepared.body.Image = image.ID
	return c.submitCreate(ctx, prepared)
}
