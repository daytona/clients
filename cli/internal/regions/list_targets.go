// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package regions

import (
	"net/url"
	"strings"
)

func normalizeBaseURL(base string) string {
	trimmed := strings.TrimRight(strings.TrimSpace(base), "/")
	u, err := url.Parse(trimmed)
	if err != nil {
		return trimmed // The HTTP client reports malformed catalog URLs.
	}
	u.Scheme = strings.ToLower(u.Scheme)
	u.Host = strings.ToLower(u.Host)
	if (u.Scheme == "https" && u.Port() == "443") || (u.Scheme == "http" && u.Port() == "80") {
		host := u.Hostname()
		if strings.Contains(host, ":") {
			host = "[" + host + "]"
		}
		u.Host = host
	}
	return u.String()
}

// ListRegionKeys maps resource regions to their endpoint's representative cursor key.
// With --region, the selected region names the cursor even if its API returns
// items from other regions. Without a catalog, the bare home cursor is used.
func (c *Catalog) ListRegionKeys(home, only string) map[string]string {
	keys := map[string]string{}
	if c == nil {
		return keys
	}
	onlyBase := ""
	if r := c.Find(only); r != nil {
		only = r.Id
		onlyBase = BaseFor(*r, home)
	}
	byBase := map[string]string{}
	for _, r := range c.OrderedRegions() {
		if c.HasListAliases() {
			keys[r.Id] = r.Id
			continue
		}
		base := BaseFor(r, home)
		key, ok := byBase[base]
		if !ok {
			key = r.Id
			byBase[base] = key
		}
		if only != "" && base == onlyBase {
			key = only
		}
		keys[r.Id] = key
	}
	return keys
}

// ForListCursor retains independent alias streams when a saved cursor has
// multiple entries for one URL. Their opaque tokens cannot safely be merged,
// even when one alias is done. Fresh and single-entry endpoint cursors coalesce.
func (c *Catalog) ForListCursor(home, cursor string) *Catalog {
	if c == nil {
		return nil
	}
	requested := SplitCursor(cursor)
	seen := map[string]bool{}
	shared := false
	for _, r := range c.OrderedRegions() {
		if _, ok := requested[r.Id]; !ok {
			continue
		}
		base := BaseFor(r, home)
		shared = shared || seen[base]
		seen[base] = true
	}
	if !shared {
		return c
	}
	aliases := map[string]bool{}
	for region := range requested {
		aliases[region] = true
	}
	return &Catalog{Regions: c.Regions, listAliases: aliases}
}

// HasListAliases reports whether pagination must track source streams
// separately from the regions carried by items from a shared API endpoint.
func (c *Catalog) HasListAliases() bool {
	return c != nil && len(c.listAliases) > 0
}
