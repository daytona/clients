// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"maps"
	"mime/multipart"
	"net/http"
	"net/url"
	"slices"
	"strconv"
)

// WriteMany uploads a map of absolute paths to bytes in one request.
// Atomic requests rename each file independently, not as a batch transaction.
// Unsupported atomic uploads return ErrAtomicUploadUnsupported without fallback.
func (f *FileSystem) WriteMany(ctx context.Context, files map[string][]byte, opts *WriteManyOptions) (
	*BulkWriteResult, error,
) {
	paths, err := filePaths(slices.Sorted(maps.Keys(files)))
	if err != nil {
		return nil, err
	}
	options := WriteManyOptions{}
	if opts != nil {
		options = *opts
	}
	if optionErr := fileTimeout(options.Timeout); optionErr != nil {
		return nil, optionErr
	}
	var body bytes.Buffer
	form := multipart.NewWriter(&body)
	for i, path := range paths {
		prefix := "files[" + strconv.Itoa(i) + "]"
		if fieldErr := form.WriteField(prefix+".path", path); fieldErr != nil {
			return nil, wrapError(fieldErr, "write bulk upload path")
		}
		part, partErr := form.CreateFormFile(prefix+".file", fileUploadName(path))
		if partErr != nil {
			return nil, wrapError(partErr, "create bulk upload part")
		}
		if _, writeErr := part.Write(files[path]); writeErr != nil {
			return nil, wrapError(writeErr, "write bulk upload bytes")
		}
	}
	if closeErr := form.Close(); closeErr != nil {
		return nil, wrapError(closeErr, "finish bulk upload")
	}
	query := make(url.Values)
	fileAtomic(query, options.Atomic)
	var raw json.RawMessage
	spec := toolboxRequest{
		Method: http.MethodPost, Path: "/files/bulk-upload", Query: query,
		Headers:  http.Header{"Content-Type": {form.FormDataContentType()}},
		Transfer: true, Timeout: options.Timeout, Result: &raw,
		BodyFactory: func(context.Context) (io.ReadCloser, error) {
			return io.NopCloser(bytes.NewReader(body.Bytes())), nil
		},
	}
	if requestErr := f.sandbox.toolbox().do(ctx, spec); requestErr != nil {
		return nil, fileTransferError(ctx, requestErr)
	}
	return parseBulkWrite(raw)
}
