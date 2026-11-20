// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"errors"
	"io"
	"mime/multipart"
	"strings"
	"sync"
)

// fileMultipart constructs only framing in memory; data stays in its reader.
type fileMultipart struct {
	reader io.Reader
	source io.Closer
	once   sync.Once
	err    error
}

func (b *fileMultipart) Read(p []byte) (int, error) { return b.reader.Read(p) }

func (b *fileMultipart) Close() error {
	b.once.Do(func() { b.err = b.source.Close() })
	return b.err
}

type fileUploadReader struct {
	ctx      context.Context
	reader   io.Reader
	progress *fileProgress
	total    *int64
	bytes    int64
}

func (r *fileUploadReader) Read(p []byte) (int, error) {
	if err := r.ctx.Err(); err != nil {
		return 0, err
	}
	if r.total != nil && r.bytes == *r.total {
		r.progress.report(r.bytes, r.total)
		if err := r.ctx.Err(); err != nil {
			return 0, err
		}
		return 0, io.EOF
	}
	if r.total != nil && int64(len(p)) > *r.total-r.bytes {
		p = p[:*r.total-r.bytes]
	}
	n, err := r.reader.Read(p)
	r.bytes += int64(n)
	if n > 0 || errors.Is(err, io.EOF) {
		r.progress.report(r.bytes, r.total)
	}
	if errors.Is(err, io.EOF) && r.total != nil && r.bytes != *r.total {
		return n, badRequest("source file changed size during upload")
	}
	if cancelErr := r.ctx.Err(); cancelErr != nil {
		return n, cancelErr
	}
	return n, err
}

type fileUploadSource struct {
	name     string
	total    *int64
	progress *fileProgress
	open     func(context.Context) (io.ReadCloser, error)
}

func (s fileUploadSource) multipart(ctx context.Context, boundary string) (io.ReadCloser, error) {
	source, err := s.open(ctx)
	if err != nil {
		return nil, err
	}
	var framing bytes.Buffer
	writer := multipart.NewWriter(&framing)
	if boundaryErr := writer.SetBoundary(boundary); boundaryErr != nil {
		return nil, errorsWithClose(wrapError(boundaryErr, "create upload boundary"), source)
	}
	name := strings.NewReplacer("\"", "_", "\r", "_", "\n", "_", "\\", "_").Replace(s.name)
	if _, partErr := writer.CreateFormFile("file", name); partErr != nil {
		return nil, errorsWithClose(wrapError(partErr, "create upload part"), source)
	}
	prefix := bytes.Clone(framing.Bytes())
	framing.Reset()
	if closeErr := writer.Close(); closeErr != nil {
		return nil, errorsWithClose(wrapError(closeErr, "finish upload framing"), source)
	}
	body := &fileMultipart{
		source: source,
		reader: io.MultiReader(bytes.NewReader(prefix), &fileUploadReader{
			ctx: ctx, reader: source, progress: s.progress, total: s.total,
		}, bytes.NewReader(framing.Bytes())),
	}
	return body, nil
}
