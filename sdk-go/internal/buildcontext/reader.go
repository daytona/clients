// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
)

// FromReader snapshots the reader's complete section as archive bytes, without
// reserializing them or closing the caller's reader. Empty or over-1-GiB sections,
// short reads and unsafe Dockerfile paths return an invalid Error.
func FromReader(ctx context.Context, reader *io.SectionReader, dockerfile string) (result *Archive, err error) {
	defer classify(&err)
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if dockerfile == "" {
		dockerfile = "Dockerfile"
	}
	name, err := relativeDockerfilePath(dockerfile)
	if err != nil {
		return nil, err
	}
	result, err = snapshotReader(ctx, reader, reader.Size())
	if err == nil {
		result.Dockerfile = name
	}
	return result, err
}

// snapshotReader hashes exactly the bytes retained for subsequent part uploads.
func snapshotReader(ctx context.Context, input io.Reader, size int64) (result *Archive, err error) {
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if size <= 0 {
		return nil, invalid("build context size must be positive", nil)
	}
	if size > MaxBytes {
		return nil, invalid("build context exceeds the 1 GiB limit", nil)
	}
	output, err := os.CreateTemp("", "daytona-context-*.tar")
	if err != nil {
		return nil, err
	}
	owned := &Archive{Path: output.Name(), filename: output.Name()}
	defer func() {
		err = errors.Join(err, output.Close())
		if err != nil {
			err = errors.Join(err, owned.Close())
			result = nil
		}
	}()
	hash := sha256.New()
	writer := tarWriter{ctx: ctx, output: io.MultiWriter(output, hash)}
	if err = writer.copyFile(input, size); err != nil {
		if errors.Is(err, io.ErrUnexpectedEOF) {
			return nil, invalid("build context is shorter than its declared size", err)
		}
		return nil, err
	}
	owned.Size = writer.total
	owned.Digest = hex.EncodeToString(hash.Sum(nil))
	return owned, nil
}
