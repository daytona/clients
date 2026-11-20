// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/http"
	"strings"
)

type toolboxClient struct{ sandbox *Sandbox }

func (t *toolboxClient) scoped() (*transport, error) {
	s := t.sandbox
	if err := s.client.ensureOpen(); err != nil {
		return nil, err
	}
	s.mu.RLock()
	endpoint := s.toolboxURL
	s.mu.RUnlock()
	if endpoint == "" {
		return nil, badRequest("sandbox handle has no toolbox URL; fetch the sandbox with Get (or pass ToolboxURL)")
	}
	return &transport{owner: s.client, baseURL: endpoint, auth: s.auth}, nil
}

func (t *toolboxClient) do(ctx context.Context, spec toolboxRequest) error {
	scoped, err := t.scoped()
	if err != nil {
		return err
	}
	return scoped.do(ctx, spec)
}

func (t *toolboxClient) stream(ctx context.Context, spec toolboxRequest) (*http.Response, error) {
	scoped, err := t.scoped()
	if err != nil {
		return nil, err
	}
	return scoped.stream(ctx, spec)
}

// connectionInfo resolves the base URL and a single Authorization header.
// Dialers retain caller cancellation and must refuse redirects themselves.
func (t *toolboxClient) connectionInfo(ctx context.Context) (string, http.Header, error) {
	scoped, err := t.scoped()
	if err != nil {
		return "", nil, err
	}
	requestCtx, scope := newRequestScope(ctx, scoped.owner, scoped.owner.config.timeout)
	defer scope.close()
	scope.kick()
	token, err := scoped.auth.bearer(requestCtx)
	if err != nil {
		return "", nil, transportError(requestCtx, err)
	}
	headers := make(http.Header)
	headers.Set("Authorization", "Bearer "+token)
	headers.Set("X-Daytona-Source", "sdk-go")
	headers.Set("X-Daytona-SDK-Version", scoped.owner.version)
	headers.Set("User-Agent", "sdk-go/"+scoped.owner.version)
	return scoped.baseURL, headers, nil
}

func scopedToolboxURL(base, sandboxID, direct string) (string, error) {
	value := base
	if direct != "" {
		value = direct
	}
	if value == "" {
		return "", nil
	}
	trimmed := strings.TrimSuffix(value, "/")
	segment, err := pathSegment(sandboxID)
	if err != nil {
		return "", err
	}
	hasPlaceholder := strings.Contains(trimmed, "{sandboxId}") || strings.Contains(trimmed, "{sandbox_id}")
	// Mask placeholders before validation; braces in an authority otherwise
	// fail URL parsing before we can identify an unsupported placement.
	masked := strings.NewReplacer("{sandboxId}", "DAYTONASANDBOXID", "{sandbox_id}", "DAYTONASANDBOXID").
		Replace(trimmed)
	parsed, parseErr := parseHTTPBase(masked)
	if parseErr != nil {
		return "", parseErr
	}
	if hasPlaceholder {
		if strings.Contains(parsed.Scheme+parsed.Host+parsed.User.String(),
			"DAYTONASANDBOXID") {
			return "", badRequest("the sandbox ID placeholder in a toolbox URL is only supported in the path")
		}
		return strings.NewReplacer("{sandboxId}", segment, "{sandbox_id}", segment).Replace(trimmed), nil
	}
	if direct != "" {
		return trimmed, nil
	}
	return trimmed + "/" + segment, nil
}
