// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

// Package buildcontext creates deterministic, bounded PAX build archives.
package buildcontext

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"sync"

	"go.daytona.com/sdk/v2/internal/dockerignore"
)

// MaxBytes is the maximum archive size in bytes, including tar padding.
const MaxBytes int64 = 1 << 30

// Error describes a local context failure without depending on public SDK types.
type Error struct {
	// Invalid marks caller input or an unsafe/changing source (BAD_REQUEST).
	Invalid bool
	// Message describes the failure.
	Message string
	// Err preserves an underlying OS, pattern, or cancellation error.
	Err error
}

// Error describes the context failure.
func (e *Error) Error() string { return e.Message }

// Unwrap preserves cancellation and filesystem errors.
func (e *Error) Unwrap() error { return e.Err }

func invalid(message string, cause error) error {
	return &Error{Invalid: true, Message: message, Err: cause}
}

func classify(err *error) {
	if *err == nil {
		return
	}
	var contextErr *Error
	if errors.As(*err, &contextErr) {
		return
	}
	var patternErr *dockerignore.Error
	*err = &Error{Invalid: errors.As(*err, &patternErr), Message: (*err).Error(), Err: *err}
}

// Archive owns a temporary tar file. Close removes it after upload, including
// after failed or cancelled builds.
type Archive struct {
	// Path is the absolute path of the temporary tar file.
	Path string
	// Size is the complete tar size in bytes.
	Size int64
	// Digest is the lowercase hexadecimal SHA-256 of the complete tar.
	Digest string
	// Dockerfile is the archive-relative Dockerfile path.
	Dockerfile string
	filename   string
	close      sync.Once
	closeErr   error
}

// Close removes the owned file. It is idempotent and safe for concurrent calls.
func (a *Archive) Close() error {
	a.close.Do(func() {
		if a.filename == "" {
			return
		}
		a.closeErr = os.Remove(a.filename)
		if errors.Is(a.closeErr, os.ErrNotExist) {
			a.closeErr = nil
		}
		classify(&a.closeErr)
	})
	return a.closeErr
}

func archive(ctx context.Context, entries []entry, dockerfile string) (result *Archive, err error) {
	defer classify(&err)
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	output, err := os.CreateTemp("", "daytona-context-*.tar")
	if err != nil {
		return nil, err
	}
	owned := &Archive{Path: output.Name(), filename: output.Name(), Dockerfile: dockerfile}
	defer func() {
		err = errors.Join(err, output.Close())
		if err != nil {
			err = errors.Join(err, owned.Close())
			result = nil
		}
	}()
	hash := sha256.New()
	writer := &tarWriter{ctx: ctx, output: io.MultiWriter(output, hash)}
	if err = writer.writeEntries(entries); err != nil {
		return nil, err
	}
	owned.Size = writer.total
	owned.Digest = hex.EncodeToString(hash.Sum(nil))
	return owned, nil
}

// FromDirectory archives directory using Dockerfile-specific ignore precedence.
// An empty dockerfilePath selects Dockerfile. Unsafe paths, unsupported entries,
// and an archive exceeding 1 GiB return an Error with Invalid set.
func FromDirectory(ctx context.Context, directory, dockerfilePath string) (result *Archive, err error) {
	defer classify(&err)
	if dockerfilePath == "" {
		dockerfilePath = "Dockerfile"
	}
	root, err := absolutePath(directory)
	if err != nil {
		return nil, err
	}
	entries, name, err := directoryEntries(ctx, root, dockerfilePath)
	if err != nil {
		return nil, err
	}
	return archive(ctx, entries, name)
}

// FromSources archives a rendered Dockerfile and declaration-time local sources.
// Source contents are read now; non-link sources cannot be redirected by links.
func FromSources(ctx context.Context, dockerfile string, sources []Source) (result *Archive, err error) {
	defer classify(&err)
	entries := []entry{{name: "Dockerfile", kind: bytesEntry, mode: 0o644, content: []byte(dockerfile)}}
	if len(sources) != 0 {
		entries = append(entries, entry{name: ".daytona-context/", kind: dirEntry, mode: 0o755})
	}
	for _, source := range sources {
		if err = ctx.Err(); err != nil {
			return nil, err
		}
		children, sourceErr := source.entries(ctx)
		if sourceErr != nil {
			return nil, sourceErr
		}
		entries = append(entries, children...)
	}
	return archive(ctx, entries, "Dockerfile")
}
