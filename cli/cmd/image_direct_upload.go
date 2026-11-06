// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"golang.org/x/sync/errgroup"
)

// Contexts above the multipart limit go straight to storage through
// POST /build-contexts, up to the platform's 1 GiB cap.
const imageContextDirectMaxBytes int64 = 1 << 30

const imageContextPartAttempts = 3

// Parts in flight at once. Each streams from the staged file, so memory stays
// a few buffers whatever the part size.
const imageContextPartConcurrency = 4

const imageContextCompleteAttempts = 3

// imageContextCompleteTimeout bounds one complete call: the API reads the
// whole stored archive back (up to 1 GiB) before it answers. Tests shorten it.
var imageContextCompleteTimeout = 10 * time.Minute

// imageContextPartBackoff is the pause before part attempt n+1; tests shorten it.
var imageContextPartBackoff = func(attempt int) time.Duration {
	return time.Duration(500<<attempt) * time.Millisecond
}

// imageContextCompleteBackoff is the pause before complete attempt n+1; tests shorten it.
var imageContextCompleteBackoff = func(attempt int) time.Duration {
	return time.Duration(1<<attempt) * time.Second
}

// imageContextStorage uploads the parts. It is not the API client: the part
// URLs carry their own signature and must never receive the API credential,
// and its errors are never returned as they name the URL.
var imageContextStorage = &http.Client{
	Timeout: 5 * time.Minute,
	CheckRedirect: func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	},
}

// stagedImageContext is the archive as a file that can be hashed and read by
// offset: a tar file as given, or a directory archived to a temporary file.
type stagedImageContext struct {
	file    *os.File
	size    int64
	sha256  string
	cleanup func()
}

// cancellableReader stops a long copy at the next read once ctx is done, so an
// interrupt does not wait for a whole archive to be hashed or written.
type cancellableReader struct {
	ctx context.Context
	r   io.Reader
}

func (c cancellableReader) Read(p []byte) (int, error) {
	if err := c.ctx.Err(); err != nil {
		return 0, err
	}
	return c.r.Read(p)
}

func stageImageContext(ctx context.Context, upload imageContextUpload) (*stagedImageContext, error) {
	if upload.file != "" {
		file, err := os.Open(upload.file)
		if err != nil {
			return nil, err
		}
		digest := sha256.New()
		size, err := io.Copy(digest, cancellableReader{ctx, file})
		if err != nil {
			_ = file.Close()
			return nil, err
		}
		return &stagedImageContext{file: file, size: size, sha256: hex.EncodeToString(digest.Sum(nil)), cleanup: func() { _ = file.Close() }}, nil
	}
	file, err := os.CreateTemp("", "daytona-context-*.tar")
	if err != nil {
		return nil, err
	}
	cleanup := func() {
		_ = file.Close()
		_ = os.Remove(file.Name())
	}
	reader, err := upload.open()
	if err != nil {
		cleanup()
		return nil, err
	}
	digest := sha256.New()
	size, err := io.Copy(io.MultiWriter(file, digest), cancellableReader{ctx, reader})
	_ = reader.Close()
	if err != nil {
		cleanup()
		return nil, err
	}
	return &stagedImageContext{file: file, size: size, sha256: hex.EncodeToString(digest.Sum(nil)), cleanup: cleanup}, nil
}

// postImageDirect uploads the context to storage, then builds from the
// verified upload.
func postImageDirect(ctx context.Context, client *api.Client, meta map[string]string, dockerfilePath string, staged *stagedImageContext) (api.Raw[gen.AsyncOperation], error) {
	var created api.Raw[gen.AsyncOperation]
	if staged.size > imageContextDirectMaxBytes {
		return created, cerr.Usage("build context exceeds the 1 GiB limit (%d bytes)", staged.size)
	}
	declare := gen.CreateBuildContextUploadRequest{Dockerfile: &dockerfilePath, Bytes: staged.size, Sha256: staged.sha256}
	if region := meta["region_id"]; region != "" {
		declare.RegionId = &region
	}
	var upload gen.BuildContextUpload
	if err := client.Do(ctx, http.MethodPost, "/build-contexts", nil, declare, &upload); err != nil {
		return created, err
	}
	if upload.State != gen.BuildContextUploadStateReady {
		if err := putImageContextParts(ctx, staged, upload.Parts); err != nil {
			return created, err
		}
		if err := completeImageContextUpload(ctx, client, upload); err != nil {
			return created, err
		}
	}
	// The build must name the region the upload was verified in.
	build := gen.CreateImageRequest{ContextUploadId: &upload.Id, RegionId: &upload.RegionId}
	if name := meta["name"]; name != "" {
		build.Name = &name
	}
	if arch := meta["cpu_arch"]; arch != "" {
		build.CpuArch = &arch
	}
	err := client.Do(ctx, http.MethodPost, "/images", nil, build, &created)
	return created, err
}

// putImageContextParts uploads every part, a bounded number at a time. The
// first failure cancels the parts still in flight and is the one returned.
func putImageContextParts(ctx context.Context, staged *stagedImageContext, parts []gen.BuildContextUploadPart) error {
	var total int64
	for _, part := range parts {
		total += part.Bytes
	}
	if total != staged.size {
		return cerr.New(cerr.ExitFailure, "build context upload parts do not cover the archive")
	}
	group, groupCtx := errgroup.WithContext(ctx)
	group.SetLimit(imageContextPartConcurrency)
	var offset int64
	for _, part := range parts {
		start := offset
		offset += part.Bytes
		group.Go(func() error { return putImageContextPart(groupCtx, staged.file, start, part) })
	}
	return group.Wait()
}

// completeImageContextUpload asks the API to verify the uploaded archive. The
// API answers a repeated complete of a verified upload with that upload, so a
// call that timed out or met a server or network failure is sent again.
func completeImageContextUpload(ctx context.Context, client *api.Client, upload gen.BuildContextUpload) error {
	long := client.WithTimeoutAtLeast(imageContextCompleteTimeout)
	complete := gen.CompleteBuildContextUploadRequest{RegionId: upload.RegionId}
	path := "/build-contexts/" + upload.Id.String() + "/complete"
	for attempt := 0; ; attempt++ {
		var completed gen.BuildContextUpload
		err := long.Do(ctx, http.MethodPost, path, nil, complete, &completed)
		if err == nil || ctx.Err() != nil || attempt+1 >= imageContextCompleteAttempts || !retryableComplete(err) {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(imageContextCompleteBackoff(attempt)):
		}
	}
}

func retryableComplete(err error) bool {
	var apiErr *api.APIError
	if errors.As(err, &apiErr) {
		return apiErr.Status >= 500 && apiErr.Status != http.StatusNotImplemented
	}
	var urlErr *url.Error
	return errors.As(err, &urlErr)
}

func putImageContextPart(ctx context.Context, file *os.File, offset int64, part gen.BuildContextUploadPart) error {
	status := 0
	for attempt := 0; attempt < imageContextPartAttempts; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodPut, part.Url, io.NewSectionReader(file, offset, part.Bytes))
		if err != nil {
			return cerr.New(cerr.ExitFailure, "build context part %d has an unusable upload URL", part.PartNumber)
		}
		req.ContentLength = part.Bytes
		resp, err := imageContextStorage.Do(req)
		if err != nil {
			// The storage client's own timeout also matches
			// context.DeadlineExceeded; only the caller's context ends the upload.
			if ctx.Err() != nil {
				return ctx.Err()
			}
			status = 0
		} else {
			_, _ = io.Copy(io.Discard, resp.Body)
			_ = resp.Body.Close()
			status = resp.StatusCode
			if status >= 200 && status < 300 {
				return nil
			}
			// Not CodeForStatus: a storage 403 is an expired or refused part
			// URL, not a missing API permission.
			if status < 500 && status != http.StatusRequestTimeout && status != http.StatusTooManyRequests {
				return cerr.New(cerr.ExitFailure,
					"storage refused build context part %d (HTTP %d); its upload URL may have expired, run the build again", part.PartNumber, status)
			}
		}
		if attempt+1 < imageContextPartAttempts {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(imageContextPartBackoff(attempt)):
			}
		}
	}
	if status == 0 {
		return cerr.New(cerr.ExitFailure, "build context part %d could not reach storage", part.PartNumber)
	}
	return cerr.New(cerr.ExitFailure, "storage did not accept build context part %d (HTTP %d)", part.PartNumber, status)
}
