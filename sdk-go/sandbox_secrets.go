// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"unicode/utf8"
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
	CaptureUnresolvedCount *int32
	// Bindings pairs each attached secret with the environment variable it is
	// injected under, in the order of Secrets; nil when none was reported.
	Bindings []SandboxSecretBinding
}

// SandboxSecretBinding is an attached secret and the environment variable it
// is injected under.
type SandboxSecretBinding struct {
	// Name is the project secret's name.
	Name string
	// Env is the environment variable the secret is injected under.
	Env string
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
	for _, name := range secrets {
		if !utf8.ValidString(name) {
			return nil, badRequest("sandbox secret names must be valid UTF-8")
		}
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
	result := &SandboxSecretsInfo{ETag: etag}
	for _, entry := range []struct {
		name   string
		target *string
	}{
		{"status", &result.Status}, {"error", &result.Error}, {"capture_status", &result.CaptureStatus},
	} {
		*entry.target, err = field[string](object, entry.name, false)
		if err != nil {
			return nil, err
		}
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
	count, err := sandboxOptionalInteger(object, "capture_unresolved_count")
	if err != nil {
		return nil, err
	}
	if count != nil {
		n := *count
		if n < math.MinInt32 || n > math.MaxInt32 {
			return nil, badRequest("response field capture_unresolved_count must be an int32")
		}
		value := int32(n)
		result.CaptureUnresolvedCount = &value
	}
	result.Bindings, err = sandboxSecretBindings(object)
	if err != nil {
		return nil, err
	}
	return result, nil
}

func sandboxSecretBindings(object jsonObject) ([]SandboxSecretBinding, error) {
	rows, err := field[[]JSONValue](object, "bindings", false)
	if err != nil || rows == nil {
		return nil, err
	}
	bindings := make([]SandboxSecretBinding, len(rows))
	for index, raw := range rows {
		var row jsonObject
		if json.Unmarshal(raw, &row) != nil || row == nil {
			return nil, badRequest("response field bindings must hold objects")
		}
		name, nameErr := field[string](row, "name", true)
		if nameErr != nil {
			return nil, nameErr
		}
		env, envErr := field[string](row, "env", true)
		if envErr != nil {
			return nil, envErr
		}
		bindings[index] = SandboxSecretBinding{Name: name, Env: env}
	}
	return bindings, nil
}
