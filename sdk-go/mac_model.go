// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package daytona

import (
	"context"
	"net/url"
	"unicode/utf8"
)

// MacModels reads the Mac model registry and advisory capacity and prices.
// Obtain this concurrency-safe service from Client.MacModels.
type MacModels struct{ client *Client }

// MacModels returns the client's Mac model service.
func (c *Client) MacModels() *MacModels { return &MacModels{client: c} }

// List returns every registered Mac model, ordered by ID. This registry is not
// paginated. Malformed response fields return ErrBadRequest.
func (s *MacModels) List(ctx context.Context) ([]MacModel, error) {
	var raw JSONValue
	if err := s.client.api().do(ctx, apiRequest{Path: "/mac-models", Result: &raw}); err != nil {
		return nil, err
	}
	object, err := parseObject(raw, "Mac model list")
	if err != nil {
		return nil, err
	}
	rows, err := field[[]JSONValue](object, "mac_models", true)
	if err != nil {
		return nil, err
	}
	models := make([]MacModel, 0, len(rows))
	for _, row := range rows {
		model, parseErr := parseMacModel(row)
		if parseErr != nil {
			return nil, parseErr
		}
		models = append(models, model)
	}
	return models, nil
}

// Catalog returns advisory free Macs and prices per model and region.
// Nil options include all regions. Availability can change before a purchase;
// a nil Price means pricing is unavailable. Invalid UTF-8 returns ErrBadRequest.
func (s *MacModels) Catalog(ctx context.Context, opts *MacCatalogOptions) (*MacCatalog, error) {
	query := make(url.Values)
	if opts != nil {
		if !utf8.ValidString(opts.Region) {
			return nil, badRequest("region must be valid UTF-8")
		}
		if opts.Region != "" {
			query.Set("region_ids", opts.Region)
		}
	}
	var raw JSONValue
	if err := s.client.api().do(ctx, apiRequest{Path: "/mac-catalog", Query: query, Result: &raw}); err != nil {
		return nil, err
	}
	return parseMacCatalog(raw)
}
