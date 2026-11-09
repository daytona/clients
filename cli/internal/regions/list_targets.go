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

// InScope reports whether region id is the region selected or a region
// inside it (its parent_region_ids name selected).
func (c *Catalog) InScope(selected, id string) bool {
	if strings.EqualFold(id, selected) {
		return true
	}
	if c == nil {
		return false
	}
	if r := c.Find(id); r != nil {
		for _, parent := range r.ParentRegionIds {
			if strings.EqualFold(parent, selected) {
				return true
			}
		}
	}
	return false
}

// scopeBases maps every API base URL serving the region only or a region
// inside it to the region that names that endpoint's cursor and errors: only
// itself for its own base, else the first region of the scope (in
// OrderedRegions order) on that base. Nil when only is not listed.
func (c *Catalog) scopeBases(home, only string) map[string]string {
	selected := c.Find(only)
	if selected == nil {
		return nil
	}
	bases := map[string]string{BaseFor(*selected, home): selected.Id}
	for _, r := range c.OrderedRegions() {
		if !c.InScope(selected.Id, r.Id) {
			continue
		}
		if base := BaseFor(r, home); bases[base] == "" {
			bases[base] = r.Id
		}
	}
	return bases
}

// ListRegionKeys maps resource regions to their endpoint's representative cursor key.
// With --region, the endpoints serving the selected region or a region inside
// it are named by scopeBases even if they return items from other regions.
// Without a catalog, the bare home cursor is used.
func (c *Catalog) ListRegionKeys(home, only string) map[string]string {
	keys := map[string]string{}
	if c == nil {
		return keys
	}
	scope := c.scopeBases(home, only)
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
		if named, ok := scope[base]; ok {
			key = named
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
