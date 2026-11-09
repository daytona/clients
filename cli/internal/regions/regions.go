// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

// Package regions resolves where a request goes: the routing catalog (the
// flat `GET /regions` list), the region tag on an external sandbox id
// (`<base58>-<region>`), and the fan-out over every API that serves a listed
// region for list commands, with per-region failures collected instead of
// aborting.
package regions

import (
	"context"
	"fmt"
	"net/http"
	"sort"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/daytona/clients/cli/internal/output"
)

// Catalog holds the GET /regions response. A region may publish an
// api_base_url; when absent, requests use the client's current API base URL.
type Catalog struct {
	Regions     []gen.Region
	listAliases map[string]bool
}

// Fetch loads GET /regions.
func Fetch(ctx context.Context, c *api.Client) (*Catalog, error) {
	var page gen.RegionList
	if err := c.Do(ctx, http.MethodGet, "/regions", nil, nil, &page); err != nil {
		return nil, err
	}
	return &Catalog{Regions: page.Regions}, nil
}

// OrderedRegions returns regions with parent entries last and IDs sorted
// within each group. FanOut and ListRegionKeys use the first region for a URL
// to label its cursors and errors, preferring a specific region over a parent.
func (c *Catalog) OrderedRegions() []gen.Region {
	covering := make(map[string]bool, len(c.Regions))
	for _, r := range c.Regions {
		for _, parent := range r.ParentRegionIds {
			covering[parent] = true
		}
	}
	out := append([]gen.Region(nil), c.Regions...)
	sort.SliceStable(out, func(i, j int) bool {
		if covering[out[i].Id] != covering[out[j].Id] {
			return !covering[out[i].Id]
		}
		return out[i].Id < out[j].Id
	})
	return out
}

// Find returns the listed region with id (case-insensitive) or nil.
func (c *Catalog) Find(id string) *gen.Region {
	for i := range c.Regions {
		if strings.EqualFold(c.Regions[i].Id, id) {
			return &c.Regions[i]
		}
	}
	return nil
}

// BaseFor returns the API base URL to use for region r: its own
// api_base_url when published, else the home base the catalog came from.
func BaseFor(r gen.Region, home string) string {
	if r.ApiBaseUrl != nil && strings.TrimSpace(*r.ApiBaseUrl) != "" {
		return normalizeBaseURL(*r.ApiBaseUrl)
	}
	return normalizeBaseURL(home)
}

// TagOf returns the region suffix of an external sandbox id, or "" for a
// bare UUID / untagged id.
func TagOf(externalID string, catalogs ...*Catalog) string {
	if looksLikeUUID(externalID) {
		return ""
	}
	if len(catalogs) > 0 && catalogs[0] != nil {
		longest := ""
		for _, region := range catalogs[0].OrderedRegions() {
			if strings.HasSuffix(externalID, "-"+region.Id) && len(region.Id) > len(longest) {
				longest = region.Id
			}
		}
		return longest
	}
	i := strings.LastIndexByte(externalID, '-')
	if i < 0 || i == len(externalID)-1 {
		return ""
	}
	return externalID[i+1:]
}

func looksLikeUUID(s string) bool {
	if len(s) != 36 {
		return false
	}
	for i, ch := range s {
		switch i {
		case 8, 13, 18, 23:
			if ch != '-' {
				return false
			}
		default:
			if !((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f') || (ch >= 'A' && ch <= 'F')) {
				return false
			}
		}
	}
	return true
}

// ClientFor returns a client targeting the region that owns externalID: the
// tag's api_base_url if the catalog knows it, else the home client.
func ClientFor(ctx context.Context, home *api.Client, cat *Catalog, externalID string) *api.Client {
	tag := TagOf(externalID, cat)
	if tag == "" || cat == nil {
		return home
	}
	if r := cat.Find(tag); r != nil {
		return home.WithBaseURL(BaseFor(*r, home.BaseURL))
	}
	return home
}

// Page is one region's page of a fan-out list.
type Page[T any] struct {
	Items      []T
	NextCursor string
}

// SplitCursor decodes a fan-out cursor ("region-a:abc,region-b:def") into per-region
// cursors; a bare cursor (no ':') applies to every region / the home client.
func SplitCursor(cursor string) map[string]string {
	out := map[string]string{}
	if cursor == "" {
		return out
	}
	for _, part := range strings.Split(cursor, ",") {
		if i := strings.IndexByte(part, ':'); i > 0 {
			out[part[:i]] = part[i+1:]
		} else {
			out[""] = part
		}
	}
	return out
}

func toRegionError(region string, err error) output.RegionError {
	re := output.RegionError{Region: region, Message: err.Error(), Err: err}
	var ae *api.APIError
	if asAPIError(err, &ae) {
		re.Status = ae.Status
		re.Code = ae.Code
		re.Source = ae.Source
		re.TraceID = ae.TraceID
		if ae.Message != "" {
			re.Message = ae.Message
		}
	}
	return re
}

func asAPIError(err error, target **api.APIError) bool {
	for err != nil {
		if ae, ok := err.(*api.APIError); ok {
			*target = ae
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}

// partialFailure marks an incomplete list as the servers' failure, not the
// CLI's: the error object then has no code or source of its own (each
// region's are in the list's `errors`).
type partialFailure struct{ msg string }

func (p partialFailure) Error() string        { return p.msg }
func (p partialFailure) ErrorCode() string    { return "" }
func (p partialFailure) ErrorSource() string  { return "" }
func (p partialFailure) ErrorTraceID() string { return "" }

// PartialError returns the exit-7 error for an incomplete list, or nil.
func PartialError[T any](l output.List[T]) error {
	if !l.Incomplete {
		return nil
	}
	var parts []string
	for _, e := range l.Errors {
		parts = append(parts, regionFailureText(e))
	}
	msg := fmt.Sprintf("incomplete results — %d region(s) failed: %s", len(l.Errors), strings.Join(parts, "; "))
	return cerr.Wrap(cerr.ExitPartial, partialFailure{msg}, "%s", msg).
		WithHint("the items shown are from the regions that answered; with -o json or -o yaml the list's `errors` holds each failure")
}

// regionFailureText is one region's failure for the human-readable message:
// the region, the HTTP status, the server's message and its error code.
func regionFailureText(e output.RegionError) string {
	text := e.Message
	if e.Status != 0 {
		text = fmt.Sprintf("%d %s", e.Status, text)
	}
	if e.Code != "" {
		text += " (" + e.Code + ")"
	}
	if e.TraceID != "" {
		text += " [trace " + e.TraceID + "]"
	}
	if e.Region != "" {
		text = e.Region + ": " + text
	}
	return text
}
