// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"crypto/rand"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
)

// WriteFile uploads exact bytes to path, optionally atomically. Nil data is an empty file.
// Four arguments follow the SDK convention: context, path, content, optional settings.
func (f *FileSystem) WriteFile(ctx context.Context, path string, data []byte, opts *WriteFileOptions) error {
	size := int64(len(data))
	source := fileUploadSource{
		name: fileUploadName(path), total: &size,
		open: func(context.Context) (io.ReadCloser, error) { return io.NopCloser(bytes.NewReader(data)), nil },
	}
	return f.uploadSource(ctx, fileUploadRequest{path: path, source: source, options: opts})
}

// UploadFile streams a local regular file without buffering it whole. A 401 can
// replay from a freshly opened file. Growth past its initial size is ignored;
// shrinkage returns ErrBadRequest. Remote atomicity is opt-in.
func (f *FileSystem) UploadFile(ctx context.Context, localPath, remotePath string, opts *UploadOptions) error {
	if _, err := filePath(remotePath); err != nil {
		return err
	}
	if opts != nil {
		if err := fileTimeout(opts.Timeout); err != nil {
			return err
		}
	}
	if err := ctx.Err(); err != nil {
		return fileTransferError(ctx, err)
	}
	info, err := os.Stat(localPath)
	if err != nil {
		return wrapError(err, "stat upload source")
	}
	if !info.Mode().IsRegular() {
		return badRequest("upload source must be a regular file")
	}
	size := info.Size()
	source := fileUploadSource{
		name: filepath.Base(localPath), total: &size,
		open: func(context.Context) (io.ReadCloser, error) {
			file, openErr := os.Open(localPath)
			if openErr != nil {
				return nil, wrapError(openErr, "open upload source")
			}
			return file, nil
		},
	}
	return f.uploadSource(ctx, fileUploadRequest{path: remotePath, source: source, options: opts})
}

// Upload streams r into a remote file. It never replays a one-shot reader after
// a 401. An io.ReadCloser is closed when the request ends; its Close must unblock
// Read. Other readers must return promptly or observe ctx themselves, since Go
// cannot interrupt an arbitrary Reader. Nil r returns ErrBadRequest.
func (f *FileSystem) Upload(ctx context.Context, path string, r io.Reader, opts *UploadOptions) (failure error) {
	if r == nil {
		return badRequest("upload reader is required")
	}
	prepared, err := fileUploadSpec(path, opts)
	if err != nil {
		return err
	}
	if contextErr := ctx.Err(); contextErr != nil {
		return fileTransferError(ctx, contextErr)
	}
	source := fileUploadSource{
		name: fileUploadName(path), progress: prepared.progress,
		open: func(context.Context) (io.ReadCloser, error) {
			if closer, ok := r.(io.ReadCloser); ok {
				return closer, nil
			}
			return io.NopCloser(r), nil
		},
	}
	body, err := source.multipart(ctx, prepared.boundary)
	if err != nil {
		return err
	}
	defer func() {
		if closeErr := body.Close(); closeErr != nil {
			failure = errors.Join(failure, wrapError(closeErr, "close upload source"))
		}
	}()
	prepared.spec.BodyReader = body
	if requestErr := f.sandbox.toolbox().do(ctx, prepared.spec); requestErr != nil {
		return fileTransferError(ctx, requestErr)
	}
	if contextErr := ctx.Err(); contextErr != nil {
		return fileTransferError(ctx, contextErr)
	}
	return nil
}

type fileUploadRequest struct {
	path    string
	source  fileUploadSource
	options *UploadOptions
}

func (f *FileSystem) uploadSource(ctx context.Context, upload fileUploadRequest) error {
	prepared, err := fileUploadSpec(upload.path, upload.options)
	if err != nil {
		return err
	}
	source := upload.source
	source.progress = prepared.progress
	prepared.spec.BodyFactory = func(attemptCtx context.Context) (io.ReadCloser, error) {
		return source.multipart(attemptCtx, prepared.boundary)
	}
	if requestErr := f.sandbox.toolbox().do(ctx, prepared.spec); requestErr != nil {
		return fileTransferError(ctx, requestErr)
	}
	if contextErr := ctx.Err(); contextErr != nil {
		return fileTransferError(ctx, contextErr)
	}
	return nil
}

type filePreparedUpload struct {
	spec     toolboxRequest
	progress *fileProgress
	boundary string
}

func fileUploadSpec(path string, opts *UploadOptions) (filePreparedUpload, error) {
	query, err := filePath(path)
	if err != nil {
		return filePreparedUpload{}, err
	}
	options := UploadOptions{}
	if opts != nil {
		options = *opts
	}
	if optionErr := fileTimeout(options.Timeout); optionErr != nil {
		return filePreparedUpload{}, optionErr
	}
	fileAtomic(query, options.Atomic)
	boundary := "daytona-" + rand.Text()
	spec := toolboxRequest{
		Method: http.MethodPost, Path: "/files/upload", Query: query, Transfer: true, Timeout: options.Timeout,
		Headers: http.Header{"Content-Type": {"multipart/form-data; boundary=" + boundary}},
	}
	return filePreparedUpload{
		spec: spec, progress: &fileProgress{callback: options.OnProgress, last: -1},
		boundary: boundary,
	}, nil
}

func fileAtomic(query url.Values, atomic bool) {
	if atomic {
		query.Set("atomic", "true")
	}
}

func fileUploadName(path string) string {
	name := path
	for i := len(path) - 1; i >= 0; i-- {
		if path[i] == '/' {
			name = path[i+1:]
			break
		}
	}
	if name == "" {
		return "file"
	}
	return name
}
