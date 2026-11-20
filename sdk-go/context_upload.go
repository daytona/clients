// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"go.daytona.com/sdk/v2/internal/buildcontext"
	wire "go.daytona.com/sdk/v2/internal/gen/api"
)

const (
	contextUploadAttempts = 3
	contextUploadWorkers  = 4
	contextUploadChunk    = 64 << 10
)

// buildContextUpload identifies an archive verified in its selected region.
type buildContextUpload struct {
	// ID is the verified upload ID, used by the image create request.
	ID string
	// Region is the actual region returned by the upload declaration.
	Region string
}

type contextUploader struct {
	client  *Client
	archive *buildcontext.Archive
	path    string
	info    os.FileInfo
	pause   func(context.Context, time.Duration) error
}

// uploadContext borrows archive; its caller owns Archive.Close. Like the sibling
// SDKs, every size uses direct upload, including archives below 64 MiB.
func (s *Images) uploadContext(
	ctx context.Context, archive *buildcontext.Archive, region string,
) (buildContextUpload, error) {
	if err := s.client.ensureOpen(); err != nil {
		return buildContextUpload{}, err
	}
	uploader, err := newContextUploader(s.client, archive)
	if err != nil {
		return buildContextUpload{}, err
	}
	uploadCtx, scope := newRequestScope(ctx, s.client, 0)
	defer scope.close()
	return uploader.upload(uploadCtx, region)
}

func newContextUploader(client *Client, archive *buildcontext.Archive) (*contextUploader, error) {
	if archive == nil || archive.Size <= 0 {
		return nil, badRequest("build context is empty")
	}
	if archive.Size > buildcontext.MaxBytes {
		return nil, badRequest("build context exceeds the 1 GiB limit")
	}
	if strings.TrimSpace(archive.Dockerfile) == "" {
		return nil, badRequest("dockerfile must be a non-empty path")
	}
	digest, err := hex.DecodeString(archive.Digest)
	if err != nil || len(digest) != 32 || archive.Digest != strings.ToLower(archive.Digest) {
		return nil, badRequest("build context sha256 must be 64 lowercase hex characters")
	}
	info, err := os.Stat(archive.Path)
	if err != nil || !info.Mode().IsRegular() {
		return nil, badRequest("build context is not a regular archive file")
	}
	if info.Size() != archive.Size {
		return nil, badRequest("build context changed while it was being uploaded")
	}
	// Initial caller symlinks are accepted, but later reopens must not follow a
	// replacement symlink. Every opened descriptor is compared with this identity.
	path, err := filepath.EvalSymlinks(archive.Path)
	if err != nil {
		return nil, wrapError(err, "resolve build context archive")
	}
	return &contextUploader{
		client: client, archive: archive, path: path, info: info, pause: contextUploadPause,
	}, nil
}

func (u *contextUploader) upload(ctx context.Context, region string) (buildContextUpload, error) {
	body := wire.CreateBuildContextUploadRequest{
		Bytes: u.archive.Size, Dockerfile: &u.archive.Dockerfile, Sha256: u.archive.Digest,
	}
	if strings.TrimSpace(region) != "" && !strings.EqualFold(strings.TrimSpace(region), "local") {
		body.RegionId = &region
	}
	var raw json.RawMessage
	if err := u.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: "/build-contexts", Body: body, Result: &raw,
	}); err != nil {
		return buildContextUpload{}, err
	}
	declaration, err := parseContextUpload(raw)
	if err != nil {
		return buildContextUpload{}, err
	}
	if declaration.ready {
		return declaration.upload, nil
	}
	if _, err = pathSegment(declaration.upload.ID); err != nil {
		return buildContextUpload{}, badRequest("build context upload response has an invalid id")
	}
	parts, err := contextUploadLayout(declaration.parts, u.archive.Size)
	if err != nil {
		return buildContextUpload{}, err
	}
	if err = u.putParts(ctx, parts); err != nil {
		return buildContextUpload{}, err
	}
	if err = u.complete(ctx, declaration.upload); err != nil {
		return buildContextUpload{}, err
	}
	return declaration.upload, nil
}

func (u *contextUploader) complete(ctx context.Context, upload buildContextUpload) error {
	segment, err := pathSegment(upload.ID)
	if err != nil {
		return err
	}
	timeout := NoTimeout
	if u.client.config.timeout > 0 {
		timeout = max(u.client.config.timeout, 10*time.Minute)
	}
	for attempt := range contextUploadAttempts {
		err = u.client.api().do(ctx, apiRequest{
			Method: http.MethodPost, Path: "/build-contexts/" + segment + "/complete",
			Body: wire.CompleteBuildContextUploadRequest{RegionId: upload.Region}, Timeout: timeout,
		})
		if err == nil || ctx.Err() != nil || !contextCompleteRetryable(err) || attempt+1 == contextUploadAttempts {
			return err
		}
		if err = u.pause(ctx, time.Second<<attempt); err != nil {
			return err
		}
	}
	return err
}

func contextCompleteRetryable(err error) bool {
	if errors.Is(err, ErrClientTimeout) || errors.Is(err, ErrConnection) {
		return true
	}
	var sdkError *Error
	return errors.As(err, &sdkError) && sdkError.StatusCode >= 500 && sdkError.StatusCode != 501
}

func contextUploadPause(ctx context.Context, duration time.Duration) error {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return transportError(ctx, ctx.Err())
	case <-timer.C:
		return nil
	}
}
