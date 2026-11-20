// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"io"
	"net/http"
	"unicode/utf8"
)

// ReadFile reads exact bytes into memory. The client cap defaults to 1 GiB;
// opts may replace it or disable it with NoLimit. Transfers use an idle timeout,
// not a total deadline. A Content-Length mismatch returns ErrConnection.
// Use Download for arbitrarily large files.
func (f *FileSystem) ReadFile(ctx context.Context, path string, opts *ReadFileOptions) ([]byte, error) {
	query, err := filePath(path)
	if err != nil {
		return nil, err
	}
	spec := toolboxRequest{Path: "/files/download", Query: query, Transfer: true}
	fileReadOptions(&spec, opts)
	data, headers, err := f.readFileResponse(ctx, spec)
	if err != nil {
		return nil, err
	}
	if isJSONMediaType(headers.Get("Content-Type")) && len(data) != 0 {
		if jsonErr := checkJSON(data); jsonErr != nil {
			return nil, jsonErr
		}
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
// A Content-Length mismatch returns ErrConnection, as with ReadFile.
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
	body, headers, err := f.readFileResponse(ctx, spec)
	if err != nil {
		return nil, err
	}
	return parseFileMultipart(body, headers.Get("Content-Type"))
}

func (f *FileSystem) readFileResponse(ctx context.Context, spec toolboxRequest) (
	body []byte, headers http.Header, failure error,
) {
	transport, err := f.sandbox.toolbox().scoped()
	if err != nil {
		return nil, nil, err
	}
	response, err := transport.stream(ctx, spec)
	if err != nil {
		return nil, nil, fileTransferError(ctx, err)
	}
	defer closeResponse(ctx, response, &failure)
	body, err = transport.readResponse(ctx, response, spec.MaxResponseBytes)
	if err != nil {
		return nil, nil, fileTransferError(ctx, err)
	}
	if response.ContentLength >= 0 && int64(len(body)) != response.ContentLength {
		return nil, nil, transportError(ctx, io.ErrUnexpectedEOF)
	}
	if response.StatusCode != http.StatusOK {
		return nil, nil, errorFromResponse(response, body)
	}
	return body, response.Header, nil
}

func fileReadOptions(spec *toolboxRequest, opts *ReadFileOptions) {
	if opts != nil {
		spec.MaxResponseBytes, spec.Timeout = opts.MaxResponseBytes, opts.Timeout
	}
}
