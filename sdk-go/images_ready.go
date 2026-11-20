// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/url"
	"strconv"
	"time"
)

// Pull imports a registry reference and waits for readiness by default.
// Wait=false returns the initial observation. Failed imports raise
// ImageBuildFailedError; an exhausted total budget raises ErrRequestTimeout.
func (s *Images) Pull(ctx context.Context, ref string, opts *PullImageOptions) (*Image, error) {
	options := PullImageOptions{}
	if opts != nil {
		options = *opts
	}
	if err := imageWaitDuration(options.Timeout); err != nil {
		return nil, err
	}
	wait := options.Wait == nil || *options.Wait
	pullCtx, cancel := artifactWaitContext(ctx, options.Timeout, "image pull did not finish before timeout")
	defer cancel()
	image, err := s.Build(pullCtx, NewImageBuilder(ref), &options.BuildImageOptions)
	if err != nil {
		return nil, artifactWaitError(pullCtx, err)
	}
	if err = pullCtx.Err(); err != nil {
		return nil, artifactWaitError(pullCtx, transportError(pullCtx, err))
	}
	if !wait {
		return image, nil
	}
	return s.ready(pullCtx, image)
}

// BuildAndWait builds an image and waits until ready, raising
// ImageBuildFailedError with the image ID and recorded error on failure.
// Its readiness timeout starts after Build, so context upload keeps its own
// bounds. A subsequent sandbox create should use image.ID and the same region.
func (s *Images) BuildAndWait(ctx context.Context, builder *ImageBuilder, opts *BuildImageWaitOptions) (*Image, error) {
	options := BuildImageWaitOptions{}
	if opts != nil {
		options = *opts
	}
	if err := imageWaitDuration(options.Timeout); err != nil {
		return nil, err
	}
	image, err := s.Build(ctx, builder, &options.BuildImageOptions)
	if err != nil {
		return nil, err
	}
	switch image.Status {
	case ImageStatusReady:
		return image, nil
	case ImageStatusFailed, ImageStatusError:
		return s.ready(ctx, image)
	case ImageStatusUnknown, ImageStatusPending, ImageStatusBuilding:
	}
	waitCtx, cancel := artifactWaitContext(ctx, options.Timeout, "image build did not finish before timeout")
	defer cancel()
	return s.ready(waitCtx, image)
}

func (s *Images) ready(ctx context.Context, initial *Image) (*Image, error) {
	image := initial
	for {
		if err := ctx.Err(); err != nil {
			return nil, artifactWaitError(ctx, transportError(ctx, err))
		}
		switch image.Status {
		case ImageStatusReady:
			return image, nil
		case ImageStatusFailed, ImageStatusError:
			message := image.Error
			if message == "" {
				message = "image " + image.ID + " build failed"
			}
			return nil, &ImageBuildFailedError{
				SDKError: newSDKError(CodeImageBuildFailed, 409, message),
				ImageID:  image.ID, BuildError: image.Error,
			}
		case ImageStatusUnknown, ImageStatusPending, ImageStatusBuilding:
		}
		started := time.Now()
		next, err := s.hold(ctx, image.ID)
		if err != nil {
			return nil, artifactWaitError(ctx, err)
		}
		image = next
		switch image.Status {
		case ImageStatusReady, ImageStatusFailed, ImageStatusError:
		case ImageStatusUnknown, ImageStatusPending, ImageStatusBuilding:
			if err = artifactPause(ctx, s.client, time.Second-time.Since(started)); err != nil {
				return nil, artifactWaitError(ctx, err)
			}
		}
	}
}

func (s *Images) hold(ctx context.Context, id string) (*Image, error) {
	path, err := artifactPath("/images", id)
	if err != nil {
		return nil, err
	}
	hold, timeout := artifactHold(ctx)
	request := apiRequest{
		Query:   url.Values{"wait": {"ready"}, "timeout_ms": {strconv.FormatInt(wireMilliseconds(hold), 10)}},
		Timeout: timeout,
	}
	reader := artifactReader[*Image]{s.client, path, parseImage}
	return reader.read(ctx, request)
}
