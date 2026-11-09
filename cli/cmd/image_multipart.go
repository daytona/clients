// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"mime/multipart"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/dockerignore"
	toolboxclient "github.com/daytona/clients/cli/internal/toolbox"
)

var imageContextReplayLimit int64 = 32 << 20

const imageContextMaxBytes int64 = 64 << 20

type imageContextUpload struct {
	name string
	size int64
	open func() (io.ReadCloser, error)
}

func postImageMultipart(ctx context.Context, client *api.Client, meta map[string]string, dockerfilePath, contextPath string) (api.Raw[gen.AsyncOperation], error) {
	contextUpload, err := imageContextUploadForPath(contextPath, dockerfilePath)
	if err != nil {
		return api.Raw[gen.AsyncOperation]{}, err
	}
	if contextUpload.size <= imageContextReplayLimit {
		return postBufferedImageMultipart(ctx, client, meta, dockerfilePath, contextUpload)
	}
	return postStreamedImageMultipart(ctx, client, meta, dockerfilePath, contextUpload)
}

// imageContextUploadForPath prepares the build context upload: a tar file as
// is, or a directory archived without the entries its .dockerignore excludes
// (the size limit counts what is uploaded).
func imageContextUploadForPath(contextPath, dockerfilePath string) (imageContextUpload, error) {
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
		if info.Size() > imageContextMaxBytes {
			return imageContextUpload{}, imageContextTooLargeError(contextPath, info.Size())
		}
		return imageContextUpload{
			name: filepath.Base(contextPath),
			size: info.Size(),
			open: func() (io.ReadCloser, error) {
				return os.Open(resolvedPath)
			},
		}, nil
	}
	filter, err := loadBuildContextFilter(resolvedPath, dockerfilePath)
	if err != nil {
		return imageContextUpload{}, err
	}
	if err := validateImageContextDirectory(resolvedPath, contextPath, filter); err != nil {
		return imageContextUpload{}, err
	}
	size, err := archivedDirectorySize(resolvedPath, filter)
	if err != nil {
		return imageContextUpload{}, err
	}
	if size > imageContextMaxBytes {
		return imageContextUpload{}, imageContextTooLargeError(contextPath, size)
	}
	return imageContextUpload{
		name: archiveUploadName(contextPath),
		size: size,
		open: func() (io.ReadCloser, error) {
			return toolboxclient.ArchiveDirectoryFiltered(resolvedPath, filter)
		},
	}, nil
}

func imageContextTooLargeError(path string, size int64) error {
	return cerr.Usage("build context %q exceeds the 64 MiB limit (%d bytes)", path, size)
}

func validateImageContextDirectory(dir, originalPath string, filter toolboxclient.ArchiveFilter) error {
	return toolboxclient.WalkArchiveEntries(dir, filter, func(_, current string, info os.FileInfo) error {
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

func postBufferedImageMultipart(ctx context.Context, client *api.Client, meta map[string]string, dockerfilePath string, contextUpload imageContextUpload) (api.Raw[gen.AsyncOperation], error) {
	reader, err := contextUpload.open()
	if err != nil {
		return api.Raw[gen.AsyncOperation]{}, err
	}
	defer reader.Close()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if err := writeImageMultipart(writer, meta, dockerfilePath, contextUpload.name, reader); err != nil {
		return api.Raw[gen.AsyncOperation]{}, err
	}
	return decodeImageMultipartResponse(ctx, client, bytes.NewReader(body.Bytes()), writer.FormDataContentType())
}

func postStreamedImageMultipart(ctx context.Context, client *api.Client, meta map[string]string, dockerfilePath string, contextUpload imageContextUpload) (api.Raw[gen.AsyncOperation], error) {
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
		return api.Raw[gen.AsyncOperation]{}, err
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

func decodeImageMultipartResponse(ctx context.Context, client *api.Client, body io.Reader, contentType string) (api.Raw[gen.AsyncOperation], error) {
	var created api.Raw[gen.AsyncOperation]
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

func archivedDirectorySize(dir string, filter toolboxclient.ArchiveFilter) (int64, error) {
	total := int64(1024)
	err := toolboxclient.WalkArchiveEntries(dir, filter, func(_, _ string, info os.FileInfo) error {
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

// loadBuildContextFilter reads the context's ignore file as Docker does:
// `<Dockerfile>.dockerignore` next to the Dockerfile when it exists, else
// `.dockerignore` at the context root. The Dockerfile and the ignore files
// are always uploaded, as Docker sends them even when a pattern matches
// them. Returns nil (keep everything) when there is no ignore file.
func loadBuildContextFilter(contextDir, dockerfilePath string) (toolboxclient.ArchiveFilter, error) {
	dockerfile := path.Clean(filepath.ToSlash(dockerfilePath))
	candidates := []string{".dockerignore"}
	if dockerfile != "." && !path.IsAbs(dockerfile) && !strings.HasPrefix(dockerfile, "../") {
		candidates = append([]string{dockerfile + ".dockerignore"}, candidates...)
	}
	for _, name := range candidates {
		raw, err := os.ReadFile(filepath.Join(contextDir, filepath.FromSlash(name)))
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			return nil, cerr.Wrap(cerr.ExitFailure, err, "read %s: %v", name, err)
		}
		patterns, err := dockerignore.ReadPatterns(bytes.NewReader(raw))
		if err != nil {
			return nil, cerr.Wrap(cerr.ExitFailure, err, "read %s: %v", name, err)
		}
		matcher, err := dockerignore.New(patterns)
		if err != nil {
			return nil, cerr.Usage("%s: %v", name, err)
		}
		// Kept by path, not by a `!` pattern: a name with `[`, `*` or `?`
		// would be read as a pattern.
		return ignoreFileFilter{Filter: dockerignore.NewFilter(matcher, ".dockerignore", name, dockerfile), name: name}, nil
	}
	return nil, nil
}

// ignoreFileFilter reports a pattern of the ignore file name that cannot be
// matched as a usage error, when the walk first evaluates it.
type ignoreFileFilter struct {
	*dockerignore.Filter
	name string
}

func (f ignoreFileFilter) Keep(rel string, isDir bool, parent any) (bool, bool, any, error) {
	keep, descend, state, err := f.Filter.Keep(rel, isDir, parent)
	var pe *dockerignore.PatternError
	if errors.As(err, &pe) {
		return false, false, nil, cerr.Usage("%s: %v", f.name, pe)
	}
	return keep, descend, state, err
}
