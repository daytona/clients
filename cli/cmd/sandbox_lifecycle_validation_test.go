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

func TestSandboxCreateFromSnapshot_rejectsUnknownLifecycleAction_beforeRequest(t *testing.T) {
	for _, flag := range []string{"--on-lifecycle-timeout", "--on-idle-timeout"} {
		t.Run(flag, func(t *testing.T) {
			// Given
			args := []string{"sandbox", "create-from-snapshot", "snap_example", flag, "pasue", "--no-wait"}
			// When
			_, _, err := runCLI(t, args, nil)
			// Then
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
			require.ErrorContains(t, err, "pasue")
		})
	}
}

func TestSandboxCreate_rejectsMalformedPersistence_beforeLifecycleSupport(t *testing.T) {
	for _, command := range []string{"create", "create-macos"} {
		for _, persistent := range []string{`"true"`, `1`, `[]`, `{}`} {
			t.Run(command+"/"+persistent, func(t *testing.T) {
				// Given
				path := filepath.Join(t.TempDir(), "create.json")
				body := `{"persistent":` + persistent + `,"lifecycle_policy":{"on_timeout":"pause"}}`
				require.NoError(t, os.WriteFile(path, []byte(body), 0o600))
				args := []string{"sandbox", command, "--file", path, "--no-wait"}
				if command == "create-macos" {
					args = append(args, "--image", "macos-example")
				}
				// When
				_, _, err := runCLI(t, args, nil)
				// Then
				require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
				require.ErrorContains(t, err, `"persistent" must be a boolean`)
			})
		}
	}
}
