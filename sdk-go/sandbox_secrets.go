// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
)

// SandboxSecretsInfo reports attached secret names and their asynchronous apply
// state. It never includes secret values.
type SandboxSecretsInfo struct {
	// Secrets names the attached project secrets.
	Secrets []string
	// DesiredRevision is the requested revision.
	DesiredRevision int64
	// ETag is the opaque token required by ReplaceSecrets.
	ETag string
	// AppliedRevision is nil until an applied revision is reported.
	AppliedRevision *int64
	// Status is the open apply-state vocabulary, such as pending or applied.
	Status string
	// Error is the server's apply failure explanation.
	Error string
	// CaptureStatus is the server's open secret-capture status vocabulary.
	CaptureStatus string
	// CaptureUnresolvedCount is nil unless an unresolved count was reported.
	CaptureUnresolvedCount *int64
}

// GetSecrets reads attached secret names, revision ETag and apply state.
func (s *Sandbox) GetSecrets(ctx context.Context) (*SandboxSecretsInfo, error) {
	return s.secretsRequest(ctx, apiRequest{})
}

// ReplaceSecrets replaces all attached project secret names; nil removes them
// all. IfMatch is required. ErrStaleSecretsRevision identifies a stale ETag;
// the SDK does not retry it. The change applies asynchronously, and only
// subsequently started processes receive the updated environment.
func (s *Sandbox) ReplaceSecrets(
	ctx context.Context, secrets []string, opts *IfMatchOptions,
) (*SandboxSecretsInfo, error) {
	if err := sandboxIfMatch(opts); err != nil {
		return nil, err
	}
	return s.secretsRequest(ctx, apiRequest{
		Method: http.MethodPut, Headers: http.Header{"If-Match": {opts.IfMatch}},
		Body: struct {
			Secrets []string `json:"secrets"`
		}{append([]string{}, secrets...)},
	})
}

func (s *Sandbox) secretsRequest(ctx context.Context, request apiRequest) (*SandboxSecretsInfo, error) {
	segment, err := pathSegment(s.id)
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	var headers http.Header
	request.Path = "/sandboxes/" + segment + "/secrets"
	request.Result, request.ResponseHeaders = &raw, &headers
	if requestErr := s.client.api().do(ctx, request); requestErr != nil {
		return nil, requestErr
	}
	object, err := parseObject(raw, "sandbox secrets")
	if err != nil {
		return nil, err
	}
	etag := headers.Get("ETag")
	if etag == "" {
		return nil, badRequest("sandbox secrets response is missing the ETag header")
	}
	result := &SandboxSecretsInfo{
		ETag: etag, Status: optionalErrorString(object, "status"),
		Error: optionalErrorString(object, "error"), CaptureStatus: optionalErrorString(object, "capture_status"),
	}
	result.Secrets, err = sandboxStringList(object, "secrets", true)
	if err != nil {
		return nil, err
	}
	result.DesiredRevision, err = integerField(object, "desired_revision", true)
	if err != nil {
		return nil, err
	}
	result.AppliedRevision, err = sandboxOptionalInteger(object, "applied_revision")
	if err != nil {
		return nil, err
	}
	result.CaptureUnresolvedCount, err = sandboxOptionalInteger(object, "capture_unresolved_count")
	if err != nil {
		return nil, err
	}
	return result, nil
}
