// Copyright Daytona Platforms Inc.
// SPDX-License-Identifier: AGPL-3.0

package cmd

import (
	"testing"

	"github.com/daytona/clients/cli/internal/cerr"
	"github.com/stretchr/testify/require"
)

func TestResourceCommands_rejectInvalidArguments_beforeRequests(t *testing.T) {
	for _, args := range [][]string{
		{"volume", "create", "disk", "--size-gib", "0"},
		{"volume", "create", "disk", "--size-gib", "-1"},
		{"registry", "set", "registry.example.com", "--username", "me", "--password", "example", "--kind", "bogus"},
		{"image", "list", "--platform", "yes"},
		{"image", "list", "--platform", ""},
		{"audit", "list", "--start-at", "yesterday"},
		{"audit", "list", "--end-at", "bogus"},
		{"audit", "list", "--end-at", ""},
		{"key", "mint", "--permission", "read:sandboxes", "--expires", "1500ms"},
		{"org-key", "mint", "--name", "key", "--permission", "read:organization", "--expires", "1500ms"},
		{"docs", "--full", "--dir", t.TempDir()},
	} {
		t.Run(args[0]+"/"+args[len(args)-2]+"/"+args[len(args)-1], func(t *testing.T) {
			// Given invalid local arguments and an API that must not be called.
			// When executing the command.
			_, _, err := runCLI(t, args, nil)
			// Then the error is usage, before any HTTP request.
			require.Equal(t, cerr.ExitUsage, cerr.ExitCode(err))
		})
	}
}
