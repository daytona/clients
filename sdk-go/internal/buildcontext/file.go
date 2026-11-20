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
	"path/filepath"
)

// FromPath prepares a directory or snapshots a caller-owned tar file. Directory
// paths use FromDirectory; tar bytes are copied unchanged into an owned temporary
// file while hashing, so later caller edits cannot change the upload. Close never
// deletes the caller's file. Empty or over-1-GiB files return an invalid Error.
// An empty dockerfilePath selects Dockerfile.
func FromPath(ctx context.Context, filename, dockerfilePath string) (result *Archive, err error) {
	defer classify(&err)
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	absolute, err := absolutePath(filename)
	if err != nil {
		return nil, err
	}
	info, err := os.Stat(absolute)
	if err != nil {
		return nil, invalid("build context "+quote(absolute)+" is not a file or directory", err)
	}
	if info.IsDir() {
		return FromDirectory(ctx, absolute, dockerfilePath)
	}
	if !info.Mode().IsRegular() {
		return nil, invalid("build context "+quote(absolute)+" is not a file; pass a tar archive", nil)
	}
	if dockerfilePath == "" {
		dockerfilePath = "Dockerfile"
	}
	name, err := relativeDockerfilePath(dockerfilePath)
	if err != nil {
		return nil, err
	}
	return hashFile(ctx, absolute, name)
}

func hashFile(ctx context.Context, absolute, dockerfilePath string) (result *Archive, err error) {
	defer func() {
		if err != nil && result != nil {
			err = errors.Join(err, result.Close())
			result = nil
		}
	}()
	info, err := os.Stat(absolute)
	if err != nil {
		return nil, err
	}
	resolved, err := filepath.EvalSymlinks(absolute)
	if err != nil {
		return nil, err
	}
	root, err := os.OpenRoot(filepath.Dir(resolved))
	if err != nil {
		return nil, invalid("build context file is not readable: "+quote(absolute), err)
	}
	defer func() { err = errors.Join(err, root.Close()) }()
	input, err := openRegular(root, filepath.Base(resolved))
	if err != nil {
		return nil, invalid("build context file is not readable: "+quote(absolute), err)
	}
	defer func() { err = errors.Join(err, input.Close()) }()
	opened, err := input.Stat()
	if err != nil {
		return nil, err
	}
	if !opened.Mode().IsRegular() || !os.SameFile(info, opened) {
		return nil, invalid("build context changed: "+quote(absolute), nil)
	}
	result, err = snapshotFile(ctx, input, opened)
	if err == nil {
		result.Dockerfile = dockerfilePath
	}
	return result, err
}

// snapshotFile owns the immutable upload artifact, independently of input's path.
func snapshotFile(ctx context.Context, input *os.File, opened os.FileInfo) (result *Archive, err error) {
	if opened.Size() == 0 {
		return nil, invalid("build context is empty", nil)
	}
	if opened.Size() > MaxBytes {
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
	if err = writer.copyFile(input, opened.Size()); err != nil {
		if errors.Is(err, io.ErrUnexpectedEOF) {
			return nil, invalid("build context changed: "+quote(input.Name()), err)
		}
		return nil, err
	}
	after, err := input.Stat()
	if err != nil {
		return nil, err
	}
	if after.Size() != opened.Size() || !after.ModTime().Equal(opened.ModTime()) {
		return nil, invalid("build context changed: "+quote(input.Name()), io.ErrUnexpectedEOF)
	}
	owned.Size = writer.total
	owned.Digest = hex.EncodeToString(hash.Sum(nil))
	return owned, nil
}
