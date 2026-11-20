// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// PreviewLink returns a preview URL and token, privately exposing port first
// if needed. Public endpoints return an empty token. Private endpoints prefer
// a newly minted token scoped to this port alone; caller-supplied sandbox
// credentials and mint failures retain a usable preview route's token.
// A mint failure is returned when the route has no URL or private token.
// Invalid or reserved preview ports return ErrBadRequest locally.
// Deleted or deleting sandboxes return ErrConflict.
func (p *Ports) PreviewLink(ctx context.Context, port int) (*PreviewLink, error) {
	if err := validatePreviewPort(port); err != nil {
		return nil, err
	}
	path, err := p.portPath(port)
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	err = p.sandbox.client.api().do(ctx, apiRequest{Path: path, Result: &raw})
	var endpoint *PreviewEndpoint
	switch {
	case errorStatus(err) == http.StatusNotFound:
		endpoint, err = p.Expose(ctx, port, nil)
	case err == nil:
		endpoint, err = parsePreviewEndpoint(raw)
	}
	if err != nil {
		return nil, err
	}
	if endpoint.Public {
		return &PreviewLink{URL: endpoint.URL}, nil
	}
	if requestErr := p.sandbox.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: path + "/preview-url", Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	object, err := parseObject(raw, "preview url")
	if err != nil {
		return nil, err
	}
	address, err := field[string](object, "url", true)
	if err != nil {
		return nil, err
	}
	token, err := field[string](object, "token", true)
	if err != nil {
		return nil, err
	}
	if address == "" {
		address = endpoint.URL
	}
	link := &PreviewLink{URL: address, Token: token, ExpiresAt: previewTokenExpiry(token)}
	return p.scopePreviewLink(ctx, port, link)
}

func (p *Ports) scopePreviewLink(ctx context.Context, port int, link *PreviewLink) (*PreviewLink, error) {
	minted, mintErr := p.sandbox.auth.previewToken(ctx, port)
	if ctx.Err() != nil {
		return nil, transportError(ctx, ctx.Err())
	}
	if errors.Is(mintErr, ErrClientClosed) {
		return nil, mintErr
	}
	if mintErr != nil && (link.URL == "" || link.Token == "") {
		return nil, mintErr
	}
	// Best-effort scoped mint failures do not discard a usable route link;
	// the scoped token never enters the toolbox auth cache.
	if minted.token != "" {
		link.Token, link.ExpiresAt = minted.token, minted.expiresAt
	}
	return link, nil
}

// SignedPreviewLink returns a self-contained URL needing no headers or other
// credentials. Nil options or zero ExpiresIn selects the 60-second server default.
// Positive lifetimes must be whole seconds; the deployment caps the maximum,
// typically seven days. Invalid ports, reserved daemon ports, and
// non-whole-second or negative lifetimes return
// ErrBadRequest locally. Unavailable sandboxes may return ErrConflict.
func (p *Ports) SignedPreviewLink(
	ctx context.Context, port int, opts *SignedPreviewLinkOptions,
) (*SignedPreviewLink, error) {
	if err := validatePreviewPort(port); err != nil {
		return nil, err
	}
	body := struct {
		ExpiresInSeconds int64 `json:"expires_in_seconds,omitempty"`
	}{}
	var expiresIn time.Duration
	if opts != nil {
		expiresIn = opts.ExpiresIn
	}
	if expiresIn < 0 || expiresIn%time.Second != 0 {
		return nil, badRequest("ExpiresIn must be a positive whole-second duration, or zero for the default")
	}
	body.ExpiresInSeconds = int64(expiresIn / time.Second)
	path, err := p.portPath(port)
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	if requestErr := p.sandbox.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: path + "/signed-preview-urls", Body: body, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	object, err := parseObject(raw, "signed preview url")
	if err != nil {
		return nil, err
	}
	address, err := field[string](object, "url", true)
	if err != nil {
		return nil, err
	}
	token, err := field[string](object, "token", true)
	if err != nil {
		return nil, err
	}
	if _, fieldErr := field[string](object, "expires_at", true); fieldErr != nil {
		return nil, fieldErr
	}
	expiry, err := timestampField(object, "expires_at")
	if err != nil {
		return nil, err
	}
	return &SignedPreviewLink{URL: address, Token: token, ExpiresAt: expiry}, nil
}
