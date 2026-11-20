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

// FromPath prepares a directory or hashes a caller-owned tar file. Directory
// paths use FromDirectory; tar bytes are uploaded unchanged and Close never
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
	if blank(dockerfilePath) {
		return nil, invalid("dockerfile must be a non-empty path", nil)
	}
	return hashFile(ctx, absolute, dockerfilePath)
}

func hashFile(ctx context.Context, absolute, dockerfilePath string) (result *Archive, err error) {
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
	defer func() {
		err = errors.Join(err, input.Close())
		if err != nil {
			result = nil
		}
	}()
	opened, err := input.Stat()
	if err != nil {
		return nil, err
	}
	if !opened.Mode().IsRegular() || !os.SameFile(info, opened) {
		return nil, invalid("build context changed: "+quote(absolute), nil)
	}
	if opened.Size() == 0 {
		return nil, invalid("build context is empty", nil)
	}
	if opened.Size() > MaxBytes {
		return nil, invalid("build context exceeds the 1 GiB limit", nil)
	}
	hash := sha256.New()
	writer := tarWriter{ctx: ctx, output: hash}
	if err = writer.copyFile(input, opened.Size()); err != nil {
		if errors.Is(err, io.ErrUnexpectedEOF) {
			return nil, invalid("build context changed: "+quote(absolute), err)
		}
		return nil, err
	}
	after, err := input.Stat()
	if err != nil {
		return nil, err
	}
	if after.Size() != opened.Size() || !after.ModTime().Equal(opened.ModTime()) {
		return nil, invalid("build context changed: "+quote(absolute), io.ErrUnexpectedEOF)
	}
	return &Archive{
		Path: absolute, Size: writer.total, Digest: hex.EncodeToString(hash.Sum(nil)), Dockerfile: dockerfilePath,
	}, nil
}
