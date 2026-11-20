// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sync"
)

// Download streams a remote file to w without a total size cap. It does not close w.
// Cancellation preserves context errors and matches ErrTransferCancelled.
// Writes must return promptly or observe ctx themselves: an arbitrary io.Writer
// cannot be forcibly interrupted. Partial output is possible on failure.
// Destination write errors preserve their cause without an ErrConnection category.
// Invalid Timeout returns ErrBadRequest before any request.
func (f *FileSystem) Download(ctx context.Context, path string, w io.Writer, opts *DownloadOptions) (failure error) {
	if w == nil {
		return badRequest("download writer is required")
	}
	response, err := f.downloadResponse(ctx, path, opts)
	if err != nil {
		return err
	}
	defer closeResponse(ctx, response, &failure)
	return copyFileDownload(ctx, w, fileDownload{response: response, options: opts})
}

type fileDownload struct {
	response *http.Response
	options  *DownloadOptions
}

func copyFileDownload(ctx context.Context, w io.Writer, download fileDownload) error {
	progress := &fileProgress{last: -1}
	if download.options != nil {
		progress.callback = download.options.OnProgress
	}
	var total *int64
	if download.response.ContentLength >= 0 {
		value := download.response.ContentLength
		total = &value
	}
	writer := &fileDownloadWriter{ctx: ctx, writer: w, progress: progress, total: total}
	if _, err := io.CopyBuffer(writer, download.response.Body, make([]byte, 64<<10)); err != nil {
		if writer.failure != nil {
			return wrapError(err, "write download destination")
		}
		return fileTransferError(ctx, err)
	}
	if total != nil && writer.bytes != *total {
		return transportError(ctx, io.ErrUnexpectedEOF)
	}
	if total != nil {
		progress.report(writer.bytes, total)
	}
	if err := ctx.Err(); err != nil {
		return fileTransferError(ctx, err)
	}
	return nil
}

type fileDownloadWriter struct {
	ctx      context.Context
	writer   io.Writer
	progress *fileProgress
	total    *int64
	bytes    int64
	failure  error
}

func (w *fileDownloadWriter) Write(p []byte) (int, error) {
	if err := w.ctx.Err(); err != nil {
		return 0, err
	}
	n, err := w.writer.Write(p)
	if err == nil && n < len(p) {
		err = io.ErrShortWrite
	}
	w.failure = err
	w.bytes += int64(n)
	if n > 0 {
		w.progress.report(w.bytes, w.total)
	}
	return n, err
}

func (f *FileSystem) downloadResponse(ctx context.Context, path string, opts *DownloadOptions) (*http.Response, error) {
	query, err := filePath(path)
	if err != nil {
		return nil, err
	}
	spec := toolboxRequest{Path: "/files/download", Query: query, Transfer: true}
	if opts != nil {
		if optionErr := fileTimeout(opts.Timeout); optionErr != nil {
			return nil, optionErr
		}
		spec.Timeout = opts.Timeout
	}
	response, err := f.sandbox.toolbox().stream(ctx, spec)
	if err != nil {
		return nil, fileTransferError(ctx, err)
	}
	return response, nil
}

// DownloadFile streams through an exclusive, private temporary file next to
// localPath, created before opening the remote stream, and renames it only on
// success. Failure leaves an existing destination
// intact and removes staging. Existing permissions are preserved; new files use
// mode 0600, avoiding process-global umask changes. Streams have no size cap.
// Invalid Timeout returns ErrBadRequest before any request.
func (f *FileSystem) DownloadFile(ctx context.Context, remotePath, localPath string, opts *DownloadOptions) (
	failure error,
) {
	staging, err := os.CreateTemp(filepath.Dir(localPath), ".daytona-dl-*")
	if err != nil {
		return wrapError(err, "create download staging file")
	}
	open, committed := true, false
	defer func() {
		if open {
			if closeErr := staging.Close(); closeErr != nil {
				failure = errors.Join(failure, wrapError(closeErr, "close download staging file"))
			}
		}
		if !committed {
			if removeErr := os.Remove(staging.Name()); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
				failure = errors.Join(failure, wrapError(removeErr, "remove download staging file"))
			}
		}
	}()
	response, err := f.downloadResponse(ctx, remotePath, opts)
	if err != nil {
		return err
	}
	finishResponse := sync.OnceFunc(func() { closeResponse(ctx, response, &failure) })
	defer finishResponse()
	failure = copyFileDownload(ctx, staging, fileDownload{response: response, options: opts})
	finishResponse()
	if failure != nil {
		return failure
	}
	info, statErr := os.Stat(localPath)
	switch {
	case statErr == nil:
		if modeErr := staging.Chmod(info.Mode().Perm()); modeErr != nil {
			return wrapError(modeErr, "preserve download destination permissions")
		}
	case !errors.Is(statErr, os.ErrNotExist):
		return wrapError(statErr, "stat download destination")
	}
	open = false
	if closeErr := staging.Close(); closeErr != nil {
		return wrapError(closeErr, "close completed download")
	}
	if contextErr := ctx.Err(); contextErr != nil {
		return fileTransferError(ctx, contextErr)
	}
	if renameErr := os.Rename(staging.Name(), localPath); renameErr != nil {
		return wrapError(renameErr, "commit download")
	}
	committed = true
	return nil
}
