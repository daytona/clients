// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: Apache-2.0

package buildcontext

import (
	"context"
	"runtime"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFromSourcesCancellationAvoidsDockerfileCopy(t *testing.T) {
	// Given an already-cancelled build with a large rendered Dockerfile.
	dockerfile := strings.Repeat("x", 8<<20)
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	var before, after runtime.MemStats
	runtime.ReadMemStats(&before)

	// When preparation is refused.
	_, err := FromSources(ctx, dockerfile, nil)
	runtime.ReadMemStats(&after)
	runtime.KeepAlive(dockerfile)

	// Then cancellation is returned without allocating a Dockerfile-sized copy.
	require.ErrorIs(t, err, context.Canceled)
	require.Less(t, after.TotalAlloc-before.TotalAlloc, uint64(len(dockerfile)/2))
}

func TestEntrySizeIncludesFinalArchiveRecordPadding(t *testing.T) {
	for _, sourceHeader := range []int64{0, tarBlock} {
		// Given the largest payload fitting in complete 10 KiB tar records.
		capacity := MaxBytes/10240*10240 - sourceHeader - tarBlock - 1024
		for _, extra := range []int64{-1, 0, 1} {
			// When checking before allocating a Dockerfile-sized byte buffer.
			err := checkEntrySize(sourceHeader, capacity+extra)
			// Then even one byte beyond the final-record budget is refused.
			if extra > 0 {
				var failure *Error
				require.ErrorAs(t, err, &failure)
				require.True(t, failure.Invalid)
			} else {
				require.NoError(t, err)
			}
		}
	}
}
