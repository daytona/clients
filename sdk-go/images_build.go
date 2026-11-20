// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"

	"go.daytona.com/sdk/v2/internal/buildcontext"
)

// Build submits a declarative builder and returns its initial observation,
// without waiting for readiness. Use ImageFromDockerfile for Dockerfile text.
// Invalid options and builder inputs return ErrBadRequest before submission.
func (s *Images) Build(ctx context.Context, builder *ImageBuilder, opts *BuildImageOptions) (image *Image, err error) {
	options := BuildImageOptions{}
	if opts != nil {
		options = *opts
	}
	request, err := imageOptions(options)
	if err != nil {
		return nil, err
	}
	if builder == nil {
		return nil, badRequest("builder must not be nil")
	}
	text, err := builder.Dockerfile()
	if err != nil {
		return nil, err
	}
	if builder.hasLocalSources() {
		if err = imageContextOptions(request); err != nil {
			return nil, err
		}
		archive, prepareErr := builder.prepare(ctx)
		if prepareErr != nil {
			return nil, prepareErr
		}
		defer func() {
			if closeErr := archive.Close(); closeErr != nil && err == nil {
				err = wrapError(closeErr, "remove temporary build context")
			}
		}()
		upload, uploadErr := s.uploadContext(ctx, archive, request.Region)
		if uploadErr != nil {
			return nil, uploadErr
		}
		request.ContextUploadID, request.Region = upload.ID, upload.Region
		return s.submit(ctx, apiRequest{Body: request})
	}
	if ref, registry := builder.registrySource(); registry {
		request.Source = &imageRegistrySource{Type: "registry", Ref: ref}
	} else {
		request.Dockerfile = text
	}
	return s.submit(ctx, apiRequest{Body: request})
}

// BuildFromContext archives a local directory or uploads an existing tar file,
// then submits a build. Caller-owned tar files are never removed.
// It honors Dockerfile-specific ignore rules, otherwise .dockerignore, and
// refuses contexts over 1 GiB. All sizes upload directly to storage, using the
// server's multipart layout for large archives, without a 64 MiB API-body cap.
// The result is an initial observation; wait using Get or BuildAndWait.
func (s *Images) BuildFromContext(
	ctx context.Context, contextPath string, opts *BuildFromContextOptions,
) (image *Image, err error) {
	options := BuildFromContextOptions{}
	if opts != nil {
		options = *opts
	}
	request, err := imageOptions(options.BuildImageOptions)
	if err != nil {
		return nil, err
	}
	if err = imageContextOptions(request); err != nil {
		return nil, err
	}
	if options.Timeout < 0 && options.Timeout != NoTimeout {
		return nil, badRequest("timeout must be non-negative or NoTimeout")
	}
	dockerfile := options.Dockerfile
	if dockerfile == "" {
		dockerfile = "Dockerfile"
	}
	if strings.TrimSpace(dockerfile) == "" {
		return nil, badRequest("dockerfile must be a non-empty path")
	}
	archive, err := buildcontext.FromPath(ctx, contextPath, dockerfile)
	if err != nil {
		return nil, imageContextError(err)
	}
	defer func() {
		if closeErr := archive.Close(); closeErr != nil && err == nil {
			err = wrapError(closeErr, "remove temporary build context")
		}
	}()
	upload, err := s.uploadContext(ctx, archive, request.Region)
	if err != nil {
		return nil, err
	}
	request.ContextUploadID, request.Region = upload.ID, upload.Region
	return s.submit(ctx, apiRequest{Body: request, Timeout: options.Timeout})
}

func (s *Images) submit(ctx context.Context, request apiRequest) (*Image, error) {
	var raw JSONValue
	request.Method, request.Path, request.Result = http.MethodPost, "/images", &raw
	if err := s.client.api().do(ctx, request); err != nil {
		return nil, err
	}
	id, err := imageAcknowledgedID(raw)
	if err != nil {
		return nil, err
	}
	return s.Get(ctx, id, nil)
}

func imageAcknowledgedID(raw JSONValue) (string, error) {
	object, err := parseObject(raw, "artifact create")
	if err != nil {
		return "", err
	}
	id, err := field[string](object, "id", true)
	if err != nil {
		return "", err
	}
	if _, err = pathSegment(id); err != nil {
		return "", err
	}
	return id, nil
}
