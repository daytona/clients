// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"strings"

	"go.daytona.com/sdk/v2/internal/buildcontext"
)

// Build submits a declarative builder and returns its initial observation,
// without waiting for readiness. Use ImageFromDockerfile for Dockerfile text.
// Invalid options and builder inputs return ErrBadRequest before submission.
func (s *Images) Build(ctx context.Context, builder *ImageBuilder, opts *BuildImageOptions) (*Image, error) {
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
		archive, prepareErr := builder.prepare(ctx)
		if prepareErr != nil {
			return nil, prepareErr
		}
		defer archive.Close() //nolint:errcheck // Best-effort local cleanup must not fail an accepted build.
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

// BuildFromContext archives a local directory or snapshots an existing tar file,
// then uploads it and submits a build. Caller-owned tar files are never removed;
// edits after preparation do not change the uploaded bytes.
// It honors Dockerfile-specific ignore rules, otherwise .dockerignore, and
// refuses contexts over 1 GiB. All sizes upload directly to storage, using the
// server's multipart layout for large archives, without a 64 MiB API-body cap.
// The result is an initial observation; wait using Get or BuildAndWait.
func (s *Images) BuildFromContext(
	ctx context.Context, contextPath string, opts *BuildFromContextOptions,
) (*Image, error) {
	return s.buildFromContext(ctx, func(ctx context.Context, dockerfile string) (*buildcontext.Archive, error) {
		return buildcontext.FromPath(ctx, contextPath, dockerfile)
	}, opts)
}

// BuildFromContextReader snapshots size archive bytes from r starting at offset
// zero, then uploads them and submits a build without waiting for readiness.
// Size must be positive and at most 1 GiB; invalid sizes, nil readers, short reads
// and unsafe Dockerfile paths return ErrBadRequest before any network request.
// Options and multipart upload behavior are the same as BuildFromContext.
// The caller retains ownership of r and must keep its bytes stable during the
// call. Reads must return promptly: context cancellation is checked between
// reads but cannot interrupt a blocking ReaderAt. The SDK never closes r.
func (s *Images) BuildFromContextReader(
	ctx context.Context, r io.ReaderAt, size int64, opts *BuildFromContextOptions,
) (*Image, error) {
	if r == nil {
		return nil, badRequest("build context reader must not be nil")
	}
	if size <= 0 {
		return nil, badRequest("build context size must be positive")
	}
	return s.buildFromContext(ctx, func(ctx context.Context, dockerfile string) (*buildcontext.Archive, error) {
		return buildcontext.FromReader(ctx, io.NewSectionReader(r, 0, size), dockerfile)
	}, opts)
}

func (s *Images) buildFromContext(
	ctx context.Context, prepare func(context.Context, string) (*buildcontext.Archive, error),
	opts *BuildFromContextOptions,
) (*Image, error) {
	options := BuildFromContextOptions{}
	if opts != nil {
		options = *opts
	}
	request, err := imageOptions(options.BuildImageOptions)
	if err != nil {
		return nil, err
	}
	if options.Timeout < 0 && options.Timeout != NoTimeout {
		return nil, badRequest("Timeout must be non-negative or NoTimeout")
	}
	dockerfile := options.Dockerfile
	if dockerfile == "" {
		dockerfile = "Dockerfile"
	}
	if strings.TrimSpace(dockerfile) == "" {
		return nil, badRequest("Dockerfile must be a non-empty path")
	}
	archive, err := prepare(ctx, dockerfile)
	if err != nil {
		return nil, imageContextError(err)
	}
	defer archive.Close() //nolint:errcheck // Best-effort local cleanup must not fail an accepted build.
	// Keep the caller's spelling on the wire; preparation already validated it.
	archive.Dockerfile = dockerfile
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
	if err = checkResponseID(id); err != nil {
		return "", err
	}
	return id, nil
}
