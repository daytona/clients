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
	"os"
	"time"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
)

// Contexts above the multipart limit go straight to storage through
// POST /build-contexts, up to the platform's 1 GiB cap.
const imageContextDirectMaxBytes int64 = 1 << 30

const imageContextPartAttempts = 3

// imageContextPartBackoff is the pause before part attempt n+1; tests shorten it.
var imageContextPartBackoff = func(attempt int) time.Duration {
	return time.Duration(500<<attempt) * time.Millisecond
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

func stageImageContext(upload imageContextUpload) (*stagedImageContext, error) {
	if upload.file != "" {
		file, err := os.Open(upload.file)
		if err != nil {
			return nil, err
		}
		digest := sha256.New()
		size, err := io.Copy(digest, file)
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
	size, err := io.Copy(io.MultiWriter(file, digest), reader)
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
		var offset int64
		for _, part := range upload.Parts {
			if err := putImageContextPart(ctx, staged.file, offset, part); err != nil {
				return created, err
			}
			offset += part.Bytes
		}
		if offset != staged.size {
			return created, cerr.New(cerr.ExitFailure, "build context upload parts do not cover the archive")
		}
		complete := gen.CompleteBuildContextUploadRequest{RegionId: &upload.RegionId}
		var completed gen.BuildContextUpload
		if err := client.Do(ctx, http.MethodPost, "/build-contexts/"+upload.Id.String()+"/complete", nil, complete, &completed); err != nil {
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
			if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
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
			if status < 500 && status != http.StatusRequestTimeout && status != http.StatusTooManyRequests {
				return cerr.New(cerr.CodeForStatus(status),
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
