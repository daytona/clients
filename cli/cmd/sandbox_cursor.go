// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"sort"
	"strings"

	gen "github.com/daytona/clients/cli/gen/api"
	"github.com/daytona/clients/cli/internal/output"
	"github.com/daytona/clients/cli/internal/regions"
)

const sandboxCursorDoneMarker = "="

type sandboxCursorState struct {
	cursor string
	done   bool
}

type listPageRequest struct {
	limit  int64
	cursor string
}

type regionalPageCursor struct {
	requested  map[string]sandboxCursorState
	regionKeys map[string]string
	streams    *pageStreams
}

func newRegionalPageCursor(cursor string, regionKeys map[string]string) regionalPageCursor {
	requested := map[string]sandboxCursorState{}
	for region, state := range parseSandboxCursor(cursor) {
		// ListRegionKeys groups aliases by normalized BaseFor URL. Re-key an
		// endpoint's saved state when its representative changes; alias streams
		// keep identity keys so their independent tokens are never collapsed.
		key, ok := regionKeys[region]
		if !ok {
			key = region
		}
		requested[key] = state
	}
	return regionalPageCursor{requested: requested, regionKeys: regionKeys,
		streams: &pageStreams{items: map[string]map[string]string{}, done: map[string]bool{}}}
}

func parseSandboxCursor(cursor string) map[string]sandboxCursorState {
	out := map[string]sandboxCursorState{}
	if cursor == "" {
		return out
	}
	for _, part := range strings.Split(cursor, ",") {
		if i := strings.IndexByte(part, ':'); i > 0 {
			value := part[i+1:]
			out[part[:i]] = sandboxCursorState{cursor: value, done: value == sandboxCursorDoneMarker}
			continue
		}
		out[""] = sandboxCursorState{cursor: part}
	}
	return out
}

func requestedSandboxCursor(region string, requested map[string]sandboxCursorState) sandboxCursorState {
	if state, ok := requested[region]; ok {
		return state
	}
	if state, ok := requested[""]; ok {
		return state
	}
	return sandboxCursorState{}
}

func trimSandboxPage(list output.List[gen.SandboxListItem], scope regionalPageCursor, limit int64) output.List[gen.SandboxListItem] {
	requested := scope.requested
	kept := list.Items
	dropped := []gen.SandboxListItem{}
	if limit > 0 && int(limit) < len(list.Items) {
		kept = append([]gen.SandboxListItem(nil), list.Items[:limit]...)
		dropped = list.Items[limit:]
	}

	lastByRegion := map[string]string{}
	serverMore := regions.SplitCursor(list.NextCursor)
	homeCursor := len(scope.regionKeys) == 0 && usesHomeSandboxCursor(list.Items, requested, serverMore)
	for _, item := range kept {
		if item.Cursor != "" {
			for regionKey, cursor := range scope.itemCursors(item.Id, item.RegionId, item.Cursor) {
				if homeCursor {
					regionKey = ""
				}
				lastByRegion[regionKey] = cursor
			}
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

func (scope regionalPageCursor) regionKey(region string) string {
	if key, ok := scope.regionKeys[region]; ok {
		return key
	}
	return region
}

// usesHomeSandboxCursor decides whether items are keyed under the bare ""
// region (a single home client answered) or per region (a multi-region
// fan-out). The decision follows the SHAPE OF THE ANSWER, never the shape of
// the requested cursor: a bare cursor carried over from a single API
// endpoint into a fan-out must not collapse the fan-out's per-region
// cursors onto "" (which would re-fetch the same page as page two).
func usesHomeSandboxCursor(items []gen.SandboxListItem, requested map[string]sandboxCursorState, serverMore map[string]string) bool {
	if len(serverMore) > 0 {
		// The server told us: a bare continuation means home; any
		// region-prefixed entry means fan-out.
		_, bare := serverMore[""]
		return bare && len(serverMore) == 1
	}
	// Last page (no continuation): use region identifiers in the results
	// to retain per-region cursor keys. Follow the requested keying only
	// when no result carries a region that the requested map keyed on.
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

func hasPendingSandboxCursor(values map[string]sandboxCursorState) bool {
	for _, value := range values {
		if !value.done {
			return true
		}
	}
	return false
}

func joinSandboxCursorMap(values map[string]sandboxCursorState) string {
	parts := make([]string, 0, len(values))
	for region, cursor := range values {
		if region == "" {
			parts = append(parts, cursor.cursor)
			continue
		}
		parts = append(parts, region+":"+cursor.cursor)
	}
	sort.Strings(parts)
	return strings.Join(parts, ",")
}
