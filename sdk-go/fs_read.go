// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"unicode/utf8"
)

// ReadFile reads exact bytes into memory. The client cap defaults to 1 GiB;
// opts may replace it or disable it with NoLimit. Transfers use an idle timeout,
// not a total deadline. Use Download for arbitrarily large files.
func (f *FileSystem) ReadFile(ctx context.Context, path string, opts *ReadFileOptions) ([]byte, error) {
	query, err := filePath(path)
	if err != nil {
		return nil, err
	}
	var data []byte
	spec := toolboxRequest{Path: "/files/download", Query: query, Transfer: true, ResponseBody: &data}
	fileReadOptions(&spec, opts)
	if requestErr := f.sandbox.toolbox().do(ctx, spec); requestErr != nil {
		return nil, fileTransferError(ctx, requestErr)
	}
	return data, nil
}

// ReadText reads strict UTF-8, preserving a leading BOM. Invalid encoding returns
// ErrBadRequest instead of silently replacing bytes. Bounds are those of ReadFile.
func (f *FileSystem) ReadText(ctx context.Context, path string, opts *ReadFileOptions) (string, error) {
	data, err := f.ReadFile(ctx, path, opts)
	if err != nil {
		return "", err
	}
	if !utf8.Valid(data) {
		return "", badRequest("file content is not valid UTF-8")
	}
	return string(data), nil
}

// WriteText writes valid UTF-8 text. Invalid Go string bytes return ErrBadRequest;
// use WriteFile for arbitrary bytes. Atomicity and progress follow UploadOptions.
func (f *FileSystem) WriteText(ctx context.Context, path, text string, opts *WriteFileOptions) error {
	if !utf8.ValidString(text) {
		return badRequest("text must be valid UTF-8")
	}
	return f.WriteFile(ctx, path, []byte(text), opts)
}

// ReadMany reads a multipart batch into memory, capped as one complete response.
// A missing file appears in Errors; an HTTP failure fails the whole call.
func (f *FileSystem) ReadMany(ctx context.Context, paths []string, opts *ReadFileOptions) (
	result *BulkReadResult, failure error,
) {
	checked, err := filePaths(paths)
	if err != nil {
		return nil, err
	}
	spec := toolboxRequest{
		Method: http.MethodPost, Path: "/files/bulk-download",
		Body: struct {
			Paths []string `json:"paths"`
		}{checked},
		Transfer: true,
	}
	fileReadOptions(&spec, opts)
	transport, err := f.sandbox.toolbox().scoped()
	if err != nil {
		return nil, err
	}
	response, err := transport.stream(ctx, spec)
	if err != nil {
		return nil, fileTransferError(ctx, err)
	}
	defer closeResponse(ctx, response, &failure)
	body, err := transport.readResponse(ctx, response, spec.MaxResponseBytes)
	if err != nil {
		return nil, fileTransferError(ctx, err)
	}
	if response.StatusCode != http.StatusOK {
		return nil, errorFromResponse(response, body)
	}
	return parseFileMultipart(body, response.Header.Get("Content-Type"))
}

func fileReadOptions(spec *toolboxRequest, opts *ReadFileOptions) {
	if opts != nil {
		spec.MaxResponseBytes, spec.Timeout = opts.MaxResponseBytes, opts.Timeout
	}
}
