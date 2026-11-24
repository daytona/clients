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
	var lookup func(context.Context, string) (*Image, error)
	if wait {
		// A waiting pull retries its first lookup like its readiness holds.
		lookup = s.retriedLookup
	}
	image, err := s.build(pullCtx, NewImageBuilder(ref), &options.BuildImageOptions, lookup)
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
	return s.ready(waitCtx, image, "image "+image.ID+" build failed")
}

// ready waits for initial to settle; failed is the message for a failure the
// server recorded no error for.
func (s *Images) ready(ctx context.Context, initial *Image, failed string) (*Image, error) {
	image := initial
	var retry readBackoff
	for {
		if err := ctx.Err(); err != nil {
			return nil, artifactRetryError(ctx, transportError(ctx, err), retry.lastErr)
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
			pause, retryable := retry.next(ctx, err)
			if !retryable {
				return nil, artifactRetryError(ctx, err, retry.lastErr)
			}
			if err = artifactPause(ctx, s.client, pause); err != nil {
				return nil, artifactRetryError(ctx, err, retry.lastErr)
			}
			continue
		}
		retry.succeeded()
		image = next
		switch image.Status {
		case ImageStatusReady, ImageStatusFailed, ImageStatusError:
		case ImageStatusUnknown, ImageStatusPending, ImageStatusBuilding:
			if err = artifactPause(ctx, s.client, time.Second-time.Since(started)); err != nil {
				return nil, artifactRetryError(ctx, err, retry.lastErr)
			}
		}
	}
}

// retriedLookup reads an image, retrying a transient failure within ctx.
func (s *Images) retriedLookup(ctx context.Context, id string) (*Image, error) {
	var retry readBackoff
	for {
		image, err := s.Get(ctx, id, nil)
		if err == nil {
			return image, nil
		}
		pause, retryable := retry.next(ctx, err)
		if !retryable {
			return nil, artifactRetryError(ctx, err, retry.lastErr)
		}
		if err = artifactPause(ctx, s.client, pause); err != nil {
			return nil, artifactRetryError(ctx, err, retry.lastErr)
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
