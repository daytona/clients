// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import "strings"

func sandboxLogDelta(seen, current string) (chunk string, truncated bool) {
	if current == seen {
		return "", false
	}
	if len(current) > len(seen) && strings.HasPrefix(current, seen) {
		return current[len(seen):], false
	}
	overlap := longestLogOverlap(seen, current)
	if overlap > 0 {
		return current[overlap:], false
	}
	return current, true
}

// longestLogOverlap returns the length of the longest suffix of `seen` that
// is a prefix of `current` — how far the new snapshot overlaps the old one
// when a later response omits older output. Linear time: the KMP failure
// function of `current`'s prefix (bounded to len(seen)) is run over the tail
// of `seen`, so a server that returns a large, mostly-different snapshot
// every poll cannot make the client do O(n²) byte comparisons.
func longestLogOverlap(seen, current string) int {
	max := len(seen)
	if len(current) < max {
		max = len(current)
	}
	if max == 0 {
		return 0
	}
	pat := current[:max]
	// Failure function of pat.
	fail := make([]int, max)
	for i, k := 1, 0; i < max; i++ {
		for k > 0 && pat[i] != pat[k] {
			k = fail[k-1]
		}
		if pat[i] == pat[k] {
			k++
		}
		fail[i] = k
	}
	// Scan the last `max` bytes of seen; the match state at the end is the
	// longest prefix of pat that is a suffix of seen.
	tail := seen[len(seen)-max:]
	k := 0
	for i := 0; i < len(tail); i++ {
		for k > 0 && tail[i] != pat[k] {
			k = fail[k-1]
		}
		if tail[i] == pat[k] {
			k++
		}
		if k == max {
			return max
		}
	}
	return k
}
