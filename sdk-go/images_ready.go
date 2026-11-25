// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
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
	return s.ready(pullCtx, image, "image pull "+image.ID+" failed")
}

// BuildAndWait builds an image and waits until ready, raising
// ImageBuildFailedError with the image ID and recorded error on failure.
// An exhausted Timeout returns ImageBuildTimeoutError with the image ID (the
// build keeps running); it matches ErrRequestTimeout.
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
		return s.ready(ctx, image, "image "+image.ID+" build failed")
	case ImageStatusUnknown, ImageStatusPending, ImageStatusBuilding:
	}
	waitCtx, cancel := artifactWaitContext(ctx, options.Timeout, "image build did not finish before timeout")
	defer cancel()
	ready, err := s.ready(waitCtx, image, "image "+image.ID+" build failed")
	return ready, buildTimeoutError(ctx, waitCtx, err, image.ID)
}

// buildTimeoutError names the still-building image when the build budget
// (waitCtx's own deadline, not the caller's context) ended the wait.
func buildTimeoutError(ctx, waitCtx context.Context, err error, imageID string) error {
	var timeout *Error
	if err == nil || ctx.Err() != nil || waitCtx.Err() == nil || !errors.As(err, &timeout) ||
		timeout.Source != SourceSDK || timeout.Code != CodeRequestTimeout {
		return err
	}
	return &ImageBuildTimeoutError{SDKError: timeout, ImageID: imageID}
}

// ready waits for initial to settle; failed is the message for a failure the
// server recorded no error for.
func (s *Images) ready(ctx context.Context, initial *Image, failed string) (*Image, error) {
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
				message = failed
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
