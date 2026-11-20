// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// SignedFileMaxTTL is the server's seven-day maximum signed-file URL lifetime.
const SignedFileMaxTTL = 7 * 24 * time.Hour

// PresignedFileURLOptions selects a signed-file URL lifetime.
type PresignedFileURLOptions struct {
	// ExpiresIn defaults to one hour. It must be whole seconds in [1s, 7 days].
	ExpiresIn time.Duration
}

// PresignedDownloadURL returns a GET link without exposing sandbox credentials.
// Unscoped direct toolbox overrides return ErrUnsupportedOperation.
func (f *FileSystem) PresignedDownloadURL(ctx context.Context, path string, opts *PresignedFileURLOptions) (
	string, error,
) {
	return f.signedURL(ctx, fileSigningRequest{
		method: http.MethodGet, route: "/files/download", path: path, opts: opts,
	})
}

// PresignedUploadURL returns a POST link accepting multipart/form-data with a file
// part named "file". The URL fixes the remote path and expires after ExpiresIn.
func (f *FileSystem) PresignedUploadURL(ctx context.Context, path string, opts *PresignedFileURLOptions) (
	string, error,
) {
	return f.signedURL(ctx, fileSigningRequest{method: http.MethodPost, route: "/files/upload", path: path, opts: opts})
}

type fileSigningRequest struct {
	method string
	route  string
	path   string
	opts   *PresignedFileURLOptions
}

func (f *FileSystem) signedURL(ctx context.Context, request fileSigningRequest) (string, error) {
	query, err := filePath(request.path)
	if err != nil {
		return "", err
	}
	ttl := time.Hour
	if request.opts != nil && request.opts.ExpiresIn != 0 {
		ttl = request.opts.ExpiresIn
	}
	if ttl < time.Second || ttl > SignedFileMaxTTL || ttl%time.Second != 0 {
		return "", badRequest("ExpiresIn must be whole seconds between one second and seven days")
	}
	base, err := f.signingBase()
	if err != nil {
		return "", err
	}
	segment, err := pathSegment(f.sandbox.ID())
	if err != nil {
		return "", err
	}
	body := struct {
		Method    string `json:"method"`
		Path      string `json:"path"`
		ExpiresAt string `json:"expires_at"`
	}{request.method, request.path, time.Now().UTC().Truncate(time.Second).Add(ttl).Format(time.RFC3339)}
	var raw json.RawMessage
	if requestErr := f.sandbox.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: "/sandboxes/" + segment + "/files/sign", Body: body, Result: &raw,
	}); requestErr != nil {
		return "", requestErr
	}
	object, err := parseObject(raw, "signed file URL")
	if err != nil {
		return "", err
	}
	signature, err := field[string](object, "signature", true)
	if err != nil {
		return "", err
	}
	if signature == "" {
		return "", badRequest("signed file URL signature must be non-empty")
	}
	if _, fieldErr := field[string](object, "expires_at", true); fieldErr != nil {
		return "", fieldErr
	}
	expires, err := timestampField(object, "expires_at")
	if err != nil {
		return "", err
	}
	query.Set("expires", strconv.FormatInt(expires.Unix(), 10))
	query.Set("signature", signature)
	return relativeURL(base, request.route, query)
}

func (f *FileSystem) signingBase() (string, error) {
	s := f.sandbox
	if err := s.client.ensureOpen(); err != nil {
		return "", err
	}
	s.mu.RLock()
	base, scoped := s.toolboxURL, s.fileSigningScoped
	s.mu.RUnlock()
	if base == "" {
		return "", badRequest("sandbox handle has no toolbox URL")
	}
	if !scoped {
		return "", newSDKError(CodeUnsupportedOperation, 400,
			"signed file URLs require a sandbox-scoped toolbox URL")
	}
	return base, nil
}

// Preserve whether the original override explicitly scoped the sandbox. Once
// core expands a placeholder it cannot be distinguished from a literal URL.
func fileSigningOverrideScoped(override, id string) bool {
	if override == "" || strings.Contains(override, "{sandboxId}") || strings.Contains(override, "{sandbox_id}") {
		return true
	}
	parsed, err := url.Parse(override)
	if err != nil {
		return false
	}
	return strings.HasSuffix(strings.TrimRight(parsed.Path, "/"), "/"+id)
}
