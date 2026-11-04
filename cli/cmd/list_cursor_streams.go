// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"sync"

	"github.com/daytona/clients/cli/internal/output"
)

// pageStreams tracks each item's source without changing its region.
// Region aliases and independent API endpoints can return the same ID.
type pageStreams struct {
	mu    sync.Mutex
	items map[string]map[string]string
	done  map[string]bool
}

func (scope regionalPageCursor) recordStream(region string, items map[string]string, more bool) {
	if scope.streams == nil {
		return
	}
	scope.streams.mu.Lock()
	defer scope.streams.mu.Unlock()
	for id, cursor := range items {
		if scope.streams.items[id] == nil {
			scope.streams.items[id] = map[string]string{}
		}
		scope.streams.items[id][region] = cursor
	}
	scope.streams.done[region] = !more
}

func (scope regionalPageCursor) itemCursors(id, region, cursor string) map[string]string {
	if scope.streams != nil {
		return scope.streams.items[id]
	}
	return map[string]string{scope.regionKey(region): cursor}
}

func (scope regionalPageCursor) completedStreams(failures []output.RegionError) map[string]sandboxCursorState {
	values := map[string]sandboxCursorState{}
	for region, state := range scope.requested {
		if state.done {
			values[region] = state
		}
	}
	if scope.streams != nil {
		for region, done := range scope.streams.done {
			if done {
				values[region] = sandboxCursorState{done: true, cursor: sandboxCursorDoneMarker}
			}
		}
	}
	for _, failure := range failures {
		region := scope.regionKey(failure.Region)
		values[region] = requestedSandboxCursor(region, scope.requested)
	}
	return values
}
