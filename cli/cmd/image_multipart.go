// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"os"
	"path/filepath"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
)

var imageContextReplayLimit int64 = 32 << 20

// imageContextMaxBytes is the largest context sent as a multipart field;
// larger ones are uploaded directly (postImageDirect).
var imageContextMaxBytes int64 = 64 << 20

type imageContextUpload struct {
	name string
	size int64
	open func() (io.ReadCloser, error)
	// file is the tar file a file context names; empty for a directory.
	file string
}

func postImageMultipart(ctx context.Context, client *api.Client, meta map[string]string, dockerfilePath, contextPath string) (gen.AsyncOperation, error) {
	contextUpload, err := imageContextUploadForPath(contextPath)
	if err != nil {
		return gen.AsyncOperation{}, err
	}
	if contextUpload.size > imageContextMaxBytes {
		staged, err := stageImageContext(ctx, contextUpload)
		if err != nil {
			return gen.AsyncOperation{}, err
		}
		defer staged.cleanup()
		return postImageDirect(ctx, client, meta, dockerfilePath, staged)
	}
	if contextUpload.size <= imageContextReplayLimit {
		return postBufferedImageMultipart(ctx, client, meta, dockerfilePath, contextUpload)
	}
	return postStreamedImageMultipart(ctx, client, meta, dockerfilePath, contextUpload)
}

func imageContextUploadForPath(contextPath string) (imageContextUpload, error) {
	resolvedPath, err := filepath.EvalSymlinks(contextPath)
	if err != nil {
		return imageContextUpload{}, err
	}
	info, err := os.Stat(resolvedPath)
	if err != nil {
		return imageContextUpload{}, err
	}
	if !info.IsDir() {
		if !info.Mode().IsRegular() {
			return imageContextUpload{}, unsupportedImageContextEntryError(contextPath, contextPath)
		}
		if info.Size() > imageContextDirectMaxBytes {
			return imageContextUpload{}, imageContextTooLargeError(contextPath, info.Size())
		}
		return imageContextUpload{
			name: filepath.Base(contextPath),
			size: info.Size(),
			open: func() (io.ReadCloser, error) {
				return os.Open(resolvedPath)
			},
			file: resolvedPath,
		}, nil
	}
	if err := validateImageContextDirectory(resolvedPath, contextPath); err != nil {
		return imageContextUpload{}, err
	}
	size, err := archivedDirectorySize(resolvedPath)
	if err != nil {
		return imageContextUpload{}, err
	}
	if size > imageContextDirectMaxBytes {
		return imageContextUpload{}, imageContextTooLargeError(contextPath, size)
	}
	return imageContextUpload{
		name: archiveUploadName(contextPath),
		size: size,
		open: func() (io.ReadCloser, error) {
			return toolboxclient.ArchiveDirectory(resolvedPath)
		},
	}, nil
}

func imageContextTooLargeError(path string, size int64) error {
	return cerr.Usage("build context %q exceeds the 1 GiB limit (%d bytes)", path, size)
}

func validateImageContextDirectory(dir, originalPath string) error {
	return filepath.Walk(dir, func(current string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		mode := info.Mode()
		if info.IsDir() || mode.IsRegular() || mode&os.ModeSymlink != 0 {
			return nil
		}
		return unsupportedImageContextEntryError(originalPath, current)
	})
}

func unsupportedImageContextEntryError(contextPath, entryPath string) error {
	return cerr.Usage("build context %q contains unsupported entry %q (only regular files, directories, and symlinks are allowed)", contextPath, entryPath)
}

func postBufferedImageMultipart(ctx context.Context, client *api.Client, meta map[string]string, dockerfilePath string, contextUpload imageContextUpload) (gen.AsyncOperation, error) {
	reader, err := contextUpload.open()
	if err != nil {
		return gen.AsyncOperation{}, err
	}
	defer reader.Close()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if err := writeImageMultipart(writer, meta, dockerfilePath, contextUpload.name, reader); err != nil {
		return gen.AsyncOperation{}, err
	}
	return decodeImageMultipartResponse(ctx, client, bytes.NewReader(body.Bytes()), writer.FormDataContentType())
}

func postStreamedImageMultipart(ctx context.Context, client *api.Client, meta map[string]string, dockerfilePath string, contextUpload imageContextUpload) (gen.AsyncOperation, error) {
	pipeReader, pipeWriter := io.Pipe()
	writer := multipart.NewWriter(pipeWriter)
	// Large context uploads stream directly into the multipart writer through an
	// io.Pipe. That keeps memory bounded, but the body is one-shot: api.Client can
	// only replay unauthorized exchanges for seekable readers, so only contexts at
	// or below imageContextReplayLimit stay buffered/retryable.
	go func() {
		reader, err := contextUpload.open()
		if err != nil {
			_ = pipeWriter.CloseWithError(err)
			return
		}
		defer reader.Close()
		if err := writeImageMultipart(writer, meta, dockerfilePath, contextUpload.name, reader); err != nil {
			_ = pipeWriter.CloseWithError(err)
			return
		}
		_ = pipeWriter.Close()
	}()
	created, err := decodeImageMultipartResponse(ctx, client, pipeReader, writer.FormDataContentType())
	if err != nil {
		_ = pipeReader.CloseWithError(err)
		return gen.AsyncOperation{}, err
	}
	return created, nil
}

func writeImageMultipart(writer *multipart.Writer, meta map[string]string, dockerfilePath, contextName string, contextReader io.Reader) error {
	for key, value := range meta {
		if value == "" {
			continue
		}
		if err := writer.WriteField(key, value); err != nil {
			return err
		}
	}
	if dockerfilePath != "" {
		if err := writer.WriteField("dockerfile", dockerfilePath); err != nil {
			return err
		}
	}
	part, err := writer.CreateFormFile("context", contextName)
	if err != nil {
		return err
	}
	if _, err := io.Copy(part, contextReader); err != nil {
		return err
	}
	return writer.Close()
}

func decodeImageMultipartResponse(ctx context.Context, client *api.Client, body io.Reader, contentType string) (gen.AsyncOperation, error) {
	var created gen.AsyncOperation
	resp, err := client.Stream(ctx, http.MethodPost, "/images", nil, body, contentType)
	if err != nil {
		return created, err
	}
	defer resp.Body.Close()
	if err := json.NewDecoder(resp.Body).Decode(&created); err != nil {
		return created, err
	}
	return created, nil
}

func archivedDirectorySize(dir string) (int64, error) {
	total := int64(1024)
	err := filepath.Walk(dir, func(current string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		rel, err := filepath.Rel(dir, current)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		total += 512
		if info.Mode().IsRegular() {
			total += info.Size()
			if rem := info.Size() % 512; rem != 0 {
				total += 512 - rem
			}
		}
		return nil
	})
	if err != nil {
		return 0, err
	}
	return total, nil
}
