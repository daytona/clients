// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package workload

import (
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestParseVolumeMounts_mountOptions(t *testing.T) {
	for _, tc := range []struct {
		input    string
		subpath  string
		readOnly bool
	}{
		{"vol:/mnt", "", false},
		{"vol:/mnt:sub", "sub", false},
		{"vol:/mnt:ro", "", true},
		{"vol:/mnt:sub:ro", "sub", true},
	} {
		t.Run(tc.input, func(t *testing.T) {
			// Given a mount string, when the parser consumes it.
			mounts, err := ParseVolumeMounts([]string{tc.input})
			// Then its path, subtree and access mode are preserved.
			require.NoError(t, err)
			require.Len(t, mounts, 1)
			require.Equal(t, "vol", mounts[0].Volume)
			require.Equal(t, "/mnt", mounts[0].MountPath)
			if tc.subpath == "" {
				require.Nil(t, mounts[0].Subpath)
			} else {
				require.Equal(t, tc.subpath, *mounts[0].Subpath)
			}
			if tc.readOnly {
				require.True(t, *mounts[0].ReadOnly)
			} else {
				require.Nil(t, mounts[0].ReadOnly)
			}
		})
	}
}

func TestParseVolumeMounts_rejectsMalformedMounts(t *testing.T) {
	for _, input := range []string{"broken", ":/mnt", "vol:", "vol:/mnt:", "vol:/mnt:sub:rw", "vol:/mnt::ro"} {
		t.Run(input, func(t *testing.T) {
			_, err := ParseVolumeMounts([]string{input})
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		})
	}
}
