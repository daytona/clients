// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package toolbox

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
)

var openUploadFile = func(path string) (io.ReadCloser, error) {
	return os.Open(path)
}

// DownloadFile fetches a pre-signed toolbox file URL to localPath. The
// client identity headers are included like every other toolbox request.
func (c *Client) DownloadFile(ctx context.Context, signedURL, localPath string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, signedURL, nil)
	if err != nil {
		return fmt.Errorf("build signed download request: %w", signedURLParseError(err))
	}
	c.inner.SetIdentityHeaders(req)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("download signed file: %w", redactURLError(err))
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return mapError(http.MethodGet, req.URL.Path, resp)
	}
	if err := os.MkdirAll(filepath.Dir(localPath), 0o755); err != nil {
		return fmt.Errorf("create local parent directory: %w", err)
	}
	return writeDownloadedFile(resp.Body, localPath)
}

func (c *Client) UploadFile(ctx context.Context, signedURL, localPath, remotePath string) error {
	file, err := openUploadFile(localPath)
	if err != nil {
		return err
	}
	defer file.Close()
	return c.UploadReader(ctx, signedURL, localPath, remotePath, file)
}

func (c *Client) UploadReader(ctx context.Context, signedURL, localPath, remotePath string, file io.Reader) error {
	req, producerDone, err := newSignedUploadRequest(ctx, signedURL, localPath, remotePath, file)
	if err != nil {
		return err
	}
	c.inner.SetIdentityHeaders(req)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		_ = req.Body.Close()
		producerErr := <-producerDone
		if producerErr != nil && !errors.Is(producerErr, io.ErrClosedPipe) {
			return producerErr
		}
		return fmt.Errorf("upload signed file: %w", redactURLError(err))
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		// Read the error body before draining it: it carries the toolbox's
		// message and code.
		mapped := mapError(http.MethodPost, req.URL.Path, resp)
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
		producerErr := <-producerDone
		if producerErr != nil && !errors.Is(producerErr, io.ErrClosedPipe) {
			return producerErr
		}
		return mapped
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
	if producerErr := <-producerDone; producerErr != nil {
		return producerErr
	}
	return nil
}

// redactURLError drops the query of a pre-signed URL from a transport error:
// its signature stays valid until it expires, so it never reaches the output.
func redactURLError(err error) error {
	var urlErr *url.Error
	if !errors.As(err, &urlErr) {
		return err
	}
	redacted := *urlErr
	if u, parseErr := url.Parse(urlErr.URL); parseErr == nil {
		u.RawQuery, u.Fragment, u.User = "", "", nil
		redacted.URL = u.String()
	} else {
		redacted.URL = "<signed URL>"
	}
	return &redacted
}

func newSignedUploadRequest(ctx context.Context, signedURL, localPath, remotePath string, file io.Reader) (*http.Request, <-chan error, error) {
	bodyReader, contentType, done := startSignedUploadBody(localPath, remotePath, file)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, signedURL, bodyReader)
	if err != nil {
		wrapped := fmt.Errorf("build signed upload request: %w", signedURLParseError(err))
		_ = bodyReader.CloseWithError(wrapped)
		<-done
		return nil, done, wrapped
	}
	req.Header.Set("Content-Type", contentType)
	return req, done, nil
}

func startSignedUploadBody(localPath, remotePath string, file io.Reader) (*io.PipeReader, string, <-chan error) {
	pr, pw := io.Pipe()
	writer := multipart.NewWriter(pw)
	done := make(chan error, 1)
	go func() {
		part, err := writer.CreateFormFile("file", path.Base(remotePath))
		if err != nil {
			wrapped := fmt.Errorf("build multipart upload for %s: %w", localPath, err)
			_ = pw.CloseWithError(wrapped)
			done <- wrapped
			close(done)
			return
		}
		if _, err := io.Copy(part, file); err != nil {
			wrapped := fmt.Errorf("read %s: %w", localPath, err)
			var pathErr *fs.PathError
			if errors.As(err, &pathErr) {
				wrapped = fmt.Errorf("read upload source: %w", err)
			}
			_ = pw.CloseWithError(wrapped)
			done <- wrapped
			close(done)
			return
		}
		if err := writer.Close(); err != nil {
			wrapped := fmt.Errorf("close multipart upload for %s: %w", localPath, err)
			_ = pw.CloseWithError(wrapped)
			done <- wrapped
			close(done)
			return
		}
		if err := pw.Close(); err != nil {
			done <- err
			close(done)
			return
		}
		done <- nil
		close(done)
	}()
	return pr, writer.FormDataContentType(), done
}

func WriteDownloadedFile(src io.Reader, localPath string) error {
	return writeDownloadedFile(src, localPath)
}

func writeDownloadedFile(src io.Reader, localPath string) error {
	return writeDownloadedFileAtomic(src, localPath)
}

func writeDownloadedFileAtomic(src io.Reader, localPath string) error {
	// Never write THROUGH a symlink at the destination: a link planted where
	// the user downloads to would redirect the write elsewhere.
	if st, err := os.Lstat(localPath); err == nil && st.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("refusing to overwrite symlink %s; remove it or choose another destination", localPath)
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("stat %s: %w", localPath, err)
	}

	parentDir := filepath.Dir(localPath)
	baseName := filepath.Base(localPath)
	tmpFile, err := os.CreateTemp(parentDir, "."+baseName+".tmp-*")
	if err != nil {
		return fmt.Errorf("create temp file for %s: %w", localPath, err)
	}
	tmpPath := tmpFile.Name()
	cleanupTmp := true
	defer func() {
		_ = tmpFile.Close()
		if cleanupTmp {
			_ = os.Remove(tmpPath)
		}
	}()

	if _, err := io.Copy(tmpFile, src); err != nil {
		return fmt.Errorf("write %s: %w", localPath, err)
	}
	if err := tmpFile.Close(); err != nil {
		return fmt.Errorf("close temp file for %s: %w", localPath, err)
	}

	if err := renameDownloadedFile(tmpPath, localPath); err != nil {
		return err
	}

	cleanupTmp = false
	return nil
}

func renameDownloadedFile(tmpPath, localPath string) error {
	if err := os.Rename(tmpPath, localPath); err == nil {
		return nil
	} else if _, statErr := os.Lstat(localPath); statErr != nil {
		return fmt.Errorf("install %s: %w", localPath, err)
	}

	if err := os.Remove(localPath); err != nil {
		return fmt.Errorf("remove existing %s before install: %w", localPath, err)
	}
	if err := os.Rename(tmpPath, localPath); err != nil {
		return fmt.Errorf("install %s: %w", localPath, err)
	}
	return nil
}
