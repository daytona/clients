// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"path/filepath"
	"slices"
	"strings"

	"go.daytona.com/sdk/v2/internal/buildcontext"
)

// AddLocalFile adds a COPY of a local regular file. Metadata is captured now,
// while contents are read at build time. A trailing '/' on remotePath appends
// the original filename. Only '~' and '~/' expand to the current home directory.
// Invalid paths are reported by Dockerfile or Build as ErrBadRequest.
func (b *ImageBuilder) AddLocalFile(localPath, remotePath string) *ImageBuilder {
	return b.localCopy(localPath, remotePath, false)
}

// AddLocalDir adds a directory's contents, honoring its root .dockerignore and
// omitting that ignore file. Build rejects links escaping the directory and
// unsupported file types. Local paths follow AddLocalFile's rules.
func (b *ImageBuilder) AddLocalDir(localPath, remotePath string) *ImageBuilder {
	return b.localCopy(localPath, remotePath, true)
}

// PipInstallFromRequirements copies a local requirements file to
// /.requirements.txt and installs it with python -m pip. Put additional pip
// options in RunCommands. Path failures follow AddLocalFile's rules.
func (b *ImageBuilder) PipInstallFromRequirements(localPath string) *ImageBuilder {
	return b.localCopy(localPath, "/.requirements.txt", false).
		RunCommands("python -m pip install -r /.requirements.txt")
}

func (b *ImageBuilder) localCopy(localPath, remotePath string, directory bool) *ImageBuilder {
	recipe := b.snapshot()
	if recipe.err != nil {
		return &recipe
	}
	if err := builderText(localPath, "ImageBuilder localPath"); err != nil {
		return b.appendSteps(nil, err)
	}
	source, err := buildcontext.NewSource(localPath, directory, len(recipe.sources)+1)
	if err != nil {
		return b.appendSteps(nil, imageContextError(err))
	}
	remote, err := builderLine(remotePath, "remote path")
	if err != nil {
		return b.appendSteps(nil, err)
	}
	if !directory && strings.HasSuffix(remote, "/") {
		filename := filepath.Base(source.LocalPath)
		remote += strings.NewReplacer(`\`, `\\`, "$", `\$`, "'", `\'`, `"`, `\"`).Replace(filename)
	}
	next := b.appendSteps([]string{"COPY " + builderArray([]string{source.ArchivePath, remote})}, nil)
	next.sources = append(slices.Clone(recipe.sources), source)
	return next
}

func (b *ImageBuilder) prepare(ctx context.Context) (*buildcontext.Archive, error) {
	dockerfile, err := b.Dockerfile()
	if err != nil {
		return nil, err
	}
	if b.hasLocalSources() && builderEscape(dockerfile) != "\\" {
		return nil, badRequest("local files need the default Dockerfile escape character")
	}
	if err = ctx.Err(); err != nil {
		return nil, transportError(ctx, err)
	}
	archive, err := buildcontext.FromSources(ctx, dockerfile, b.sources)
	if err != nil {
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return nil, transportError(ctx, err)
		}
		return nil, imageContextError(err)
	}
	return archive, nil
}

func imageContextError(err error) error {
	if err == nil {
		return nil
	}
	var failure *buildcontext.Error
	if errors.As(err, &failure) && failure.Invalid {
		result := badRequest(failure.Message)
		result.Err = err
		return result
	}
	if errors.Is(err, context.DeadlineExceeded) {
		result := newSDKError(CodeRequestTimeout, 408, "operation timed out")
		result.Err = err
		result.category = ErrClientTimeout
		return result
	}
	if errors.Is(err, context.Canceled) {
		return wrapError(err, "operation cancelled")
	}
	return wrapError(err, "prepare build context")
}
