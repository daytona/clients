// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"context"
	"net/url"

	"github.com/daytona/clients/cli/gen/api"
	apiinternal "github.com/daytona/clients/cli/internal/api"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/regions"
)

func (source regionalListSource) fetchImagePage(ctx context.Context, request listPageRequest, filters imageListFilters) (output.List[api.ImageListItem], error) {
	// A deployment with search serves the list from it, every region at once.
	// --region is checked here but not sent: the search route's region_ids
	// matches an image's home region only, while an image is listed in every
	// region it is available in (region_ids), so the command filters the
	// search items itself, as it does the list route's.
	if _, err := source.searchRegion(ctx); err != nil {
		return output.List[api.ImageListItem]{}, err
	}
	query := imageListQuery(listQuery(pageFlags{limit: request.limit}, request.cursor), filters)
	searched, served, err := getRawSearchPage[api.ImageListItem](ctx, source.search, source.home, "/images/search", query, "images")
	if served {
		if err != nil {
			return output.List[api.ImageListItem]{}, err
		}
		for _, item := range searched.Items {
			addRaw(source.raws, item)
		}
		return output.List[api.ImageListItem]{Items: apiinternal.Values(searched.Items), NextCursor: searched.NextCursor}, nil
	}
	cat, err := source.catalog.Fetch(ctx, source.home)
	if err != nil {
		return output.List[api.ImageListItem]{}, err
	}
	cat = cat.ForListCursor(source.home.BaseURL, request.cursor)
	scope := newRegionalPageCursor(request.cursor, cat.ListRegionKeys(source.home.BaseURL, source.region))
	raw, err := regions.FanOut(ctx, source.home, cat, source.region,
		func(ctx context.Context, client *apiinternal.Client, currentRegion string) (regions.Page[api.ImageListItem], error) {
			state := requestedSandboxCursor(currentRegion, scope.requested)
			if state.done {
				return regions.Page[api.ImageListItem]{Items: []api.ImageListItem{}}, nil
			}
			query := imageListQuery(listQuery(pageFlags{limit: request.limit}, state.cursor), filters)
			page, err := getRawList[api.ImageListItem](ctx, client, "/images", query, "images")
			if err != nil {
				return regions.Page[api.ImageListItem]{}, err
			}
			items := apiinternal.Values(page.Items)
			for _, item := range page.Items {
				addRaw(source.raws, item)
			}
			if scope.streams != nil {
				rows := make(map[string]string, len(items))
				for _, item := range items {
					rows[item.Id] = item.Cursor
				}
				scope.recordStream(currentRegion, rows, page.NextCursor != "")
			}
			return regions.Page[api.ImageListItem]{Items: items, NextCursor: page.NextCursor}, nil
		},
		func(a, b api.ImageListItem) bool {
			if a.CreatedAt.Equal(b.CreatedAt) {
				return a.Id > b.Id
			}
			return a.CreatedAt.After(b.CreatedAt)
		},
	)
	if err != nil {
		return output.List[api.ImageListItem]{}, err
	}
	raw.Items = regions.Unique(raw.Items, func(item api.ImageListItem) string { return item.Id })
	return trimImagePage(raw, scope, request.limit), nil
}

// imageListQuery adds the image list filters to query; the search and the
// list route take the same names.
func imageListQuery(query url.Values, filters imageListFilters) url.Values {
	if filters.name != "" {
		query.Set("name", filters.name)
	}
	if filters.anonymous != "" {
		query.Set("anonymous", filters.anonymous)
	}
	if filters.platform != "" {
		query.Set("platform", filters.platform)
	}
	if filters.sandboxID != "" {
		query.Set("sandbox_id", filters.sandboxID)
	}
	return query
}

func trimImagePage(list output.List[api.ImageListItem], scope regionalPageCursor, limit int64) output.List[api.ImageListItem] {
	requested := scope.requested
	kept := list.Items
	dropped := []api.ImageListItem{}
	if limit > 0 && int(limit) < len(list.Items) {
		kept = append([]api.ImageListItem(nil), list.Items[:limit]...)
		dropped = list.Items[limit:]
	}

	lastByRegion := map[string]string{}
	serverMore := regions.SplitCursor(list.NextCursor)
	homeCursor := len(scope.regionKeys) == 0 && usesHomeImageCursor(list.Items, requested, serverMore)
	for _, item := range kept {
		for regionKey, cursor := range scope.itemCursors(item.Id, item.RegionId, item.Cursor) {
			if homeCursor {
				regionKey = ""
			}
			lastByRegion[regionKey] = cursor
		}
	}
	needsRetry := map[string]struct{}{}
	for _, item := range dropped {
		for regionKey := range scope.itemCursors(item.Id, item.RegionId, item.Cursor) {
			if homeCursor {
				regionKey = ""
			}
			needsRetry[regionKey] = struct{}{}
		}
	}

	cursorByRegion := scope.completedStreams(list.Errors)
	for region := range needsRetry {
		if last, ok := lastByRegion[region]; ok {
			cursorByRegion[region] = sandboxCursorState{cursor: last}
			continue
		}
		cursorByRegion[region] = sandboxCursorState{cursor: requestedSandboxCursor(region, requested).cursor}
	}
	for region := range serverMore {
		if _, retry := needsRetry[region]; retry {
			continue
		}
		if last, ok := lastByRegion[region]; ok {
			cursorByRegion[region] = sandboxCursorState{cursor: last}
			continue
		}
		if state, ok := requested[region]; ok || requestedSandboxCursor(region, requested).cursor != "" {
			if !ok {
				state = requestedSandboxCursor(region, requested)
			}
			cursorByRegion[region] = sandboxCursorState{cursor: state.cursor}
			continue
		}
		cursorByRegion[region] = sandboxCursorState{cursor: serverMore[region]}
	}
	for region := range lastByRegion {
		if _, more := serverMore[region]; more {
			continue
		}
		if _, retry := needsRetry[region]; retry {
			continue
		}
		cursorByRegion[region] = sandboxCursorState{done: true, cursor: sandboxCursorDoneMarker}
	}

	list.Items = kept
	if hasPendingSandboxCursor(cursorByRegion) {
		list.NextCursor = joinSandboxCursorMap(cursorByRegion)
	} else {
		list.NextCursor = ""
	}
	return list
}

func usesHomeImageCursor(items []api.ImageListItem, requested map[string]sandboxCursorState, serverMore map[string]string) bool {
	if len(serverMore) > 0 {
		_, bare := serverMore[""]
		return bare && len(serverMore) == 1
	}
	for _, item := range items {
		if item.RegionId != "" {
			if _, perRegion := requested[item.RegionId]; perRegion {
				return false
			}
		}
	}
	_, hasHome := requested[""]
	if !hasHome {
		return false
	}
	for region := range requested {
		if region != "" {
			return false
		}
	}
	return true
}
