// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"iter"
	"net/http"
)

// ListEndpointsOptions selects one page of preview endpoints.
type ListEndpointsOptions struct {
	// Cursor continues a preceding page; empty starts at the beginning.
	Cursor string
	// Limit is 1..200; zero uses the server default.
	Limit int
}

// ExposePortOptions controls preview visibility.
type ExposePortOptions struct {
	// Public permits credential-free access; false (the default) requires a token.
	Public bool
}

// ListEndpoints returns one page of preview endpoints.
// Deleted or deleting sandboxes return ErrConflict.
func (p *Ports) ListEndpoints(ctx context.Context, opts *ListEndpointsOptions) (*Page[PreviewEndpoint], error) {
	options := ListEndpointsOptions{}
	if opts != nil {
		options = *opts
	}
	query, err := pageQuery(options.Cursor, options.Limit)
	if err != nil {
		return nil, err
	}
	path, err := p.portsPath()
	if err != nil {
		return nil, err
	}
	var raw JSONValue
	if requestErr := p.sandbox.client.api().do(ctx, apiRequest{
		Path: path, Query: query, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	object, err := parseObject(raw, "preview endpoint list")
	if err != nil {
		return nil, err
	}
	rows, err := field[[]JSONValue](object, "ports", true)
	if err != nil {
		return nil, err
	}
	cursor, err := field[string](object, "next_cursor", false)
	if err != nil {
		return nil, err
	}
	page := &Page[PreviewEndpoint]{Items: make([]PreviewEndpoint, 0, len(rows)), NextCursor: cursor}
	for _, row := range rows {
		endpoint, parseErr := parsePreviewEndpoint(row)
		if parseErr != nil {
			return nil, parseErr
		}
		page.Items = append(page.Items, *endpoint)
	}
	return page, nil
}

// ListEndpointsAll traverses pages until exhaustion, cursor repetition, an
// error, cancellation, or the consumer stopping. Options are copied immediately.
func (p *Ports) ListEndpointsAll(ctx context.Context, opts *ListEndpointsOptions) iter.Seq2[PreviewEndpoint, error] {
	options := ListEndpointsOptions{}
	if opts != nil {
		options = *opts
	}
	return paginate(ctx, options.Cursor, func(ctx context.Context, cursor string) (*Page[PreviewEndpoint], error) {
		return p.ListEndpoints(ctx, &ListEndpointsOptions{Cursor: cursor, Limit: options.Limit})
	})
}

// Expose registers port, or updates an existing endpoint's visibility.
// Pass &ExposePortOptions{Public: true} for public, credential-free access:
// the returned PreviewEndpoint.URL opens in a browser with no token. Browsers
// may first see a preview warning page (unless the organization turned it
// off); scripts and headless browsers skip it by sending the header
// PreviewWarningSkipHeader with PreviewWarningSkipHeaderValue.
// Nil options select private access. Ports outside 1..65535 and reserved
// daemon ports 2280, 2281, 22220 return ErrBadRequest before any request.
// Deleted or deleting sandboxes return ErrConflict.
func (p *Ports) Expose(ctx context.Context, port int, opts *ExposePortOptions) (*PreviewEndpoint, error) {
	if err := validatePreviewPort(port); err != nil {
		return nil, err
	}
	path, err := p.portPath(port)
	if err != nil {
		return nil, err
	}
	body := struct {
		Public bool `json:"public"`
	}{}
	if opts != nil {
		body.Public = opts.Public
	}
	var raw JSONValue
	if requestErr := p.sandbox.client.api().do(ctx, apiRequest{
		Method: http.MethodPut, Path: path, Body: body, Result: &raw,
	}); requestErr != nil {
		return nil, requestErr
	}
	return parsePreviewEndpoint(raw)
}

// Unexpose removes a preview endpoint, including while its sandbox is deleting.
// Ports outside 1..65535 return ErrBadRequest locally; reserved ports are
// accepted here so an existing registration can always be removed.
func (p *Ports) Unexpose(ctx context.Context, port int) error {
	if err := validatePort(port); err != nil {
		return err
	}
	path, err := p.portPath(port)
	if err != nil {
		return err
	}
	return p.sandbox.client.api().do(ctx, apiRequest{Method: http.MethodDelete, Path: path})
}

// ExpireSignedPreviewLinks revokes every signed preview link on every sandbox
// port, including while deleting. Links created afterwards remain usable.
func (p *Ports) ExpireSignedPreviewLinks(ctx context.Context) error {
	path, err := p.portsPath()
	if err != nil {
		return err
	}
	return p.sandbox.client.api().do(ctx, apiRequest{
		Method: http.MethodPost, Path: path[:len(path)-len("/ports")] + "/signed-preview-urls/revoke",
	})
}
