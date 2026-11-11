// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestVolumeCreate_invalidSizeNamesFlagAndBodyField(t *testing.T) {
	for _, source := range []string{"flag", "file"} {
		t.Run(source, func(t *testing.T) {
			// Given
			args := []string{"volume", "create", "data"}
			if source == "file" {
				path := filepath.Join(t.TempDir(), "volume.json")
				require.NoError(t, os.WriteFile(path, []byte(`{"size_gib":0}`), 0o600))
				args = append(args, "--file", path)
			} else {
				args = append(args, "--size-gib", "0")
			}
			// When
			_, _, err := runCLI(t, args, nil)
			// Then
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
			require.ErrorContains(t, err, "size_gib")
			require.ErrorContains(t, err, "--size-gib")
		})
	}
}
