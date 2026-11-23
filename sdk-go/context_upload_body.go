// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"errors"
	"io"
	"os"
	"sync"
)

type contextPartBody struct {
	file      *os.File
	reader    *io.SectionReader
	scope     *requestScope
	closeOnce sync.Once
	closeErr  error
}

func (u *contextUploader) checkArchive() error {
	info, err := os.Lstat(u.path)
	if err != nil || !u.matchesArchive(info) {
		return badRequest("build context changed while it was being uploaded")
	}
	return nil
}

func (u *contextUploader) matchesArchive(info os.FileInfo) bool {
	return info.Mode().IsRegular() && os.SameFile(u.info, info) && info.Size() == u.archive.Size &&
		info.ModTime().Equal(u.info.ModTime())
}

func (u *contextUploader) openPart(
	scope *requestScope, part contextUploadPart,
) (body *contextPartBody, failure error) {
	if scope.ctx.Err() != nil {
		return nil, transportError(scope.ctx, scope.ctx.Err())
	}
	// The open itself must not wait for a FIFO writer. Validate the descriptor,
	// rather than trusting a path check performed before this open.
	file, err := openContextArchive(u.path)
	if err != nil {
		return nil, badRequest("build context changed while it was being uploaded")
	}
	defer func() {
		if body == nil {
			if closeErr := file.Close(); closeErr != nil {
				failure = errors.Join(failure, wrapError(closeErr, "close build context archive"))
			}
		}
	}()
	info, err := file.Stat()
	if err != nil {
		return nil, wrapError(err, "stat build context archive")
	}
	if !u.matchesArchive(info) {
		return nil, badRequest("build context changed while it was being uploaded")
	}
	if scope.ctx.Err() != nil {
		return nil, transportError(scope.ctx, scope.ctx.Err())
	}
	return &contextPartBody{
		file: file, reader: io.NewSectionReader(file, part.offset, part.bytes), scope: scope,
	}, nil
}

func (b *contextPartBody) Read(data []byte) (int, error) {
	if b.scope.ctx.Err() != nil {
		return 0, b.scope.ctx.Err()
	}
	n, err := b.reader.Read(data[:min(len(data), contextUploadChunk)])
	if n > 0 {
		b.scope.kick()
	}
	return n, err
}

func (b *contextPartBody) Close() error {
	b.closeOnce.Do(func() { b.closeErr = b.file.Close() })
	return b.closeErr
}
